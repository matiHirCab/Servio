"""Loop principal de Servio (JES-6).

Cada `--interval` segundos (30 por defecto, tiempo del video):
    frame → recorte de cada mesa → classify() → suavizado → observación → alerts

- Las mesas se clasifican en paralelo (una llamada por mesa), así una lectura
  no tarda la suma de todas.
- Una mesa cuya lectura falla (`None` o error) no manda observación esa vez.
- Cada observación va a `alerts.process()` (MAT-5), que la escribe en
  `out/eventos.jsonl` (líneas `"event": "observation"`, junto con los avisos):
  ese log es lo que reproduce `tools/replay.py`. Los logs van a stderr.

    python main.py --source data/videos/demo.mp4 --classifier fake
    python main.py --source data/videos/demo.mp4 --classifier hybrid --demo --speed 10
"""

import argparse
import logging
import os
import sys
import warnings
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import numpy as np
from dotenv import load_dotenv

from servio_vision import alerts, prompt
from servio_vision.alerts.config import DEFAULT_INTERVAL, AlertConfig, load_config
from servio_vision.alerts.models import TableObservation
from servio_vision.capture import open_source
from servio_vision.classifier import Classifier, available_classifiers, get_classifier
from servio_vision.dish_match import DishMatcher
from servio_vision.restaurant import DEFAULT_DIR as RESTAURANT_DIR
from servio_vision.restaurant import Restaurant, plate_order
from servio_vision.schemas import Item, TableReport
from servio_vision.smoothing import TableSmoother
from servio_vision.tables import TableDetector, TableTracker
from servio_vision.zones import (
    Zones,
    crop_table,
    load_zones,
    remap_bbox,
    table_rect,
    to_jpeg,
)

logger = logging.getLogger("servio")

MAX_TABLE_WORKERS = 8  # mesas que se clasifican a la vez

ObservationHandler = Callable[[TableObservation], object]


class Pipeline:
    """Una lectura = un frame: clasifica y suaviza cada mesa y arma su observación."""

    def __init__(
        self,
        classifier: Classifier,
        zones: Zones | None,
        on_observation: ObservationHandler,
        finished_after: float | None = None,
        tables: TableTracker | None = None,
        restaurant: Restaurant | None = None,
        dish_matcher: DishMatcher | None = None,
    ) -> None:
        """`zones` fijas (de un archivo) o `tables`, que detecta las mesas en cada lectura."""
        if (zones is None) == (tables is None):
            raise ValueError("Pipeline necesita zones o tables, no las dos")
        self.classifier = classifier
        self.zones = zones or Zones(frame_size=(1, 1), tables=[])
        self.tables = tables
        self.restaurant = restaurant
        self.dish_matcher = dish_matcher
        self.on_observation = on_observation
        self.finished_after = finished_after
        self.smoothers: dict[str, TableSmoother] = {}
        self._rects: dict[str, tuple[int, int, int, int]] = {}  # recorte de cada mesa
        self.previous: dict[str, list[Item]] = {}
        self._add_tables(self.zones)
        self._pool = ThreadPoolExecutor(max_workers=MAX_TABLE_WORKERS)

    def _follow_moved_tables(self, frame: np.ndarray) -> None:
        """Si el recorte de una mesa cambió, las cajas de sus platos (relativas al
        recorte) se pasan al recorte nuevo: así el clasificador y el suavizado siguen
        reconociendo los mismos platos aunque la mesa se haya movido."""
        for zone in self.zones.tables:
            try:
                rect = table_rect(frame, zone, self.zones)
            except ValueError:
                continue
            old = self._rects.get(zone.table_id)
            self._rects[zone.table_id] = rect
            if old is None or old == rect:
                continue

            def remap(bbox, old=old, new=rect):
                return remap_bbox(bbox, old, new)

            self.previous[zone.table_id] = [
                i.model_copy(update={"bbox": remap(i.bbox)}) if i.bbox else i
                for i in self.previous[zone.table_id]
            ]
            self.smoothers[zone.table_id].remap_boxes(remap)
            ids = getattr(self.classifier, "ids", None)  # memoria de ids (YOLO)
            if ids is not None:
                ids.remap(zone.table_id, remap)

    def _identify_dishes(
        self, frame: np.ndarray, table_id: str, items: list[Item]
    ) -> None:
        """Platos nuevos de la mesa: cuál del pedido es cada uno (una vez por plato)."""
        if self.restaurant is None or self.dish_matcher is None:
            return
        zone = next((z for z in self.zones.tables if z.table_id == table_id), None)
        if zone is None:
            return
        for item in sorted(items, key=lambda i: plate_order(f"{table_id}-{i.id}")):
            track_id = f"{table_id}-{item.id}"
            if item.bbox is None or self.restaurant.is_assigned(table_id, track_id):
                continue
            options = self.restaurant.unassigned(table_id)
            if not options:
                return
            x0, y0, x1, y1 = table_rect(frame, zone, self.zones)
            bx, by, bw, bh = item.bbox
            w, h = x1 - x0, y1 - y0
            crop = frame[
                int(y0 + by * h) : int(y0 + (by + bh) * h),
                int(x0 + bx * w) : int(x0 + (bx + bw) * w),
            ]
            if crop.size == 0:
                continue
            dish = self.dish_matcher.match(to_jpeg(crop), options)
            if dish is not None:
                self.restaurant.assign(table_id, track_id, dish)

    def _only_ordered(self, table_id: str, items: list[Item]) -> list[Item]:
        """Con la comanda, un "plato" que no es ninguno de los pedidos de la mesa no
        es un plato (una caja, una taza…): no cuenta para los avisos ni se dibuja."""
        if self.restaurant is None or not self.restaurant.has_order(table_id):
            return items
        kept = []
        for item in items:
            track_id = f"{table_id}-{item.id}"
            if self.restaurant.is_assigned(table_id, track_id) or self.restaurant.dish(
                table_id, track_id
            ):
                kept.append(item)
        self.previous[table_id] = kept
        return kept

    def _add_tables(self, zones: Zones) -> None:
        for t in zones.tables:
            self.smoothers.setdefault(t.table_id, TableSmoother(self.finished_after))
            self.previous.setdefault(t.table_id, [])

    def process_frame(self, frame: np.ndarray, ts: datetime) -> list[TableObservation]:
        """Procesa un frame. Un error en una mesa descarta esa mesa, no corta el loop."""
        if self.tables is not None:
            try:
                self.zones = self.tables.update(frame)
                self._add_tables(self.zones)
                self._follow_moved_tables(frame)
            except Exception:
                logger.exception("%s: no se pudieron detectar las mesas", ts)
        futures = {}
        for zone in self.zones.tables:
            try:
                jpeg = to_jpeg(crop_table(frame, zone, self.zones))
            except Exception:
                logger.exception("%s %s: no se pudo recortar", ts, zone.table_id)
                continue
            futures[zone.table_id] = self._pool.submit(
                self._classify, jpeg, zone.table_id
            )
        observations = []
        for table_id, future in futures.items():
            report = future.result()
            if report is None:
                logger.warning("%s %s: lectura fallida, se descarta", ts, table_id)
                continue
            try:
                items = self.smoothers[table_id].update(report.items, ts)
                self.previous[table_id] = items
                if self.restaurant is not None:
                    self.restaurant.release_missing(
                        table_id, {f"{table_id}-{i.id}" for i in items}
                    )
                self._identify_dishes(frame, table_id, items)
                items = self._only_ordered(table_id, items)
                observation = self._observation(table_id, ts, report, items)
            except Exception:
                logger.exception("%s %s: no se pudo armar la observación", ts, table_id)
                continue
            try:
                self.on_observation(observation)
            except Exception:
                logger.exception(
                    "%s %s: alerts falló con esta observación", ts, table_id
                )
            observations.append(observation)
        return observations

    def close(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)

    def _classify(self, jpeg: bytes, table_id: str) -> TableReport | None:
        try:
            return self.classifier.classify(jpeg, table_id, self.previous[table_id])
        except Exception:
            logger.exception("%s: el clasificador falló", table_id)
            return None

    def _observation(
        self, table_id: str, ts: datetime, report: TableReport, items: list[Item]
    ) -> TableObservation:
        return TableObservation(
            schema_version=1,
            camera_id=self.zones.camera_id,
            ts=ts,
            table_id=table_id,
            people_present=report.people_present,
            items=[
                {
                    "track_id": f"{table_id}-{item.id}",
                    "state": item.state,
                    "fill_ratio": item.fill_ratio,
                    "confidence": item.confidence,
                    "position": item.position,
                }
                for item in items
            ],
        )


def observation_handler(
    config: AlertConfig, no_telegram: bool, restaurant: Restaurant | None = None
) -> ObservationHandler:
    """`alerts.process`, ya configurado con los umbrales resueltos (`--interval`,
    `--demo`), con Telegram o consola según `--no-telegram` y, si hay, con los datos
    del restaurante (plato y mozo en los avisos, y respuestas de los mozos)."""
    alerts.configure(config, telegram=not no_telegram, restaurant=restaurant)
    return alerts.process


def load_restaurant(directory: Path | None) -> Restaurant | None:
    """Datos del restaurante (carta, pedidos, mozos); None si no hay."""
    directory = directory or RESTAURANT_DIR
    if not (directory / "salon.json").exists():
        logger.info(
            "Sin datos del restaurante en %s: avisos sin plato ni mozo", directory
        )
        return None
    restaurant = Restaurant.load(directory)
    logger.info(
        "Datos del restaurante: %d mesas, %d platos",
        len(restaurant.tables),
        len(restaurant.dishes),
    )
    return restaurant


def classifier_kwargs(args: argparse.Namespace) -> dict:
    if args.classifier in ("yolo", "hybrid"):
        # umbral del detector: con videos de baja calidad YOLOE da confianzas bajas
        return {} if args.yolo_conf is None else {"conf": args.yolo_conf}
    if args.classifier != "fake":
        return {}
    kwargs: dict = {"mode": args.fake_mode}
    if args.fixture:
        kwargs["fixture"] = args.fixture
    return kwargs


# Plato en por_terminar tanto tiempo → vacio (quedan restos). Producción: 6 min.
PROD_FINISHED_AFTER = 6 * 60
DEMO_FINISHED_READINGS = 6  # en demo, 6 lecturas (30 s con --interval 5)


def finished_after_seconds(args: argparse.Namespace, demo: bool) -> float:
    """--finished-after > FINISHED_AFTER del .env > demo (6 lecturas) o producción."""
    if args.finished_after is not None:
        return args.finished_after
    raw = os.getenv("FINISHED_AFTER", "").strip()
    if raw:
        return float(raw)
    return DEMO_FINISHED_READINGS * args.interval if demo else PROD_FINISHED_AFTER


def demo_enabled(flag: bool) -> bool:
    """--demo le gana a DEMO_MODE (09-convenciones.md)."""
    return flag or os.getenv("DEMO_MODE", "").strip().lower() in {"1", "true", "yes"}


def interval_seconds(raw: str) -> float:
    """`--interval`: segundos, al menos 1 (los umbrales de alerts se cuentan en
    segundos enteros)."""
    value = float(raw)
    if value < 1:
        raise argparse.ArgumentTypeError(f"tiene que ser al menos 1 (vino {raw})")
    return value


def build_parser(
    description: str = "Servio: loop principal.",
) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--source", required=True, help="video, carpeta, 0 o URL")
    parser.add_argument("--classifier", default="fake", choices=available_classifiers())
    parser.add_argument(
        "--zones",
        type=Path,
        help="zonas de mesas a mano (zones.json); sin esto, las mesas se detectan solas",
    )
    parser.add_argument(
        "--interval",
        type=interval_seconds,
        default=DEFAULT_INTERVAL,
        help="segundos entre lecturas (tiempo del video, mínimo 1)",
    )
    parser.add_argument(
        "--speed", type=float, help="reproducir el video a esta velocidad"
    )
    parser.add_argument("--demo", action="store_true", help="umbrales de demo")
    parser.add_argument(
        "--no-telegram", action="store_true", help="no mandar a Telegram"
    )
    parser.add_argument("--limit", type=int, help="cortar tras N lecturas")
    parser.add_argument(
        "--verbose", action="store_true", help="mostrar todo en la terminal (depurar)"
    )
    parser.add_argument("--fake-mode", default="script", choices=["script", "replay"])
    parser.add_argument("--fixture", type=Path, help="jsonl para --fake-mode replay")
    parser.add_argument(
        "--finished-after",
        type=float,
        help="segundos en por_terminar para pasar a vacio (por defecto 6 min; demo 6 lecturas)",
    )
    parser.add_argument(
        "--restaurante",
        type=Path,
        help="carpeta con carta.json y salon.json (por defecto fixtures/restaurante)",
    )
    parser.add_argument(
        "--yolo-conf",
        type=float,
        help="umbral de confianza del detector (yolo/hybrid; por defecto 0.25)",
    )
    return parser


# Librerías que llenan la terminal: solo sus advertencias y errores.
NOISY_LOGGERS = (
    "httpx",
    "httpx2",
    "httpcore",
    "anthropic",
    "ultralytics",
    "PIL",
    "urllib3",
)


def _quiet_libraries(record: logging.LogRecord) -> bool:
    noisy = record.name.split(".")[0] in NOISY_LOGGERS
    return not noisy or record.levelno >= logging.WARNING


def setup_logging(verbose: bool = False) -> None:
    """Terminal limpia: arranque, mesas y platos nuevos, avisos y errores.
    Con `--verbose`, todo (cada llamada a Claude, pedidos HTTP, advertencias)."""
    # La consola de Windows (cp1252) no puede escribir los emojis de los avisos:
    # sin esto, el aviso impreso con --no-telegram tira UnicodeEncodeError.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )
    if verbose:
        return
    # en el handler y no con setLevel: el SDK de Anthropic vuelve a subir httpx a INFO
    for handler in logging.getLogger().handlers:
        handler.addFilter(_quiet_libraries)
    # advertencias de torch/ultralytics (FutureWarning…): no son errores nuestros
    warnings.filterwarnings("ignore", module=r"(torch|ultralytics|torchvision)")
    warnings.filterwarnings("ignore", category=FutureWarning)


ENV_FILE = alerts.VISION_DIR.parent / ".env"


def build_pipeline(args: argparse.Namespace) -> Pipeline | None:
    """Zonas, umbrales y clasificador según los flags. `None` si algo falta (ya logueado)."""
    # antes de calcular los umbrales: si no, ALERT_T1/T2/REMINDER y DEMO_MODE del
    # .env no se aplican (alerts.configure lo cargaba recién después)
    load_dotenv(ENV_FILE)  # no pisa variables ya puestas
    zones: Zones | None = None
    tables: TableTracker | None = None
    if args.zones is None:
        try:
            tables = TableTracker(TableDetector())
        except ImportError:
            logger.error(
                "Para detectar las mesas solas hace falta ultralytics "
                "(pip install -r requirements-yolo.txt), o pasá --zones config/zones.json."
            )
            return None
    elif not args.zones.exists():
        logger.error(
            "No existe %s. Sacá --zones para que las mesas se detecten solas, o "
            "generalo con tools/draw_zones.py --source <video>.",
            args.zones,
        )
        return None
    else:
        zones = load_zones(args.zones)
    try:
        config = load_config(round(args.interval), demo_enabled(args.demo))
    except ValueError as e:
        logger.error(
            "Umbrales de aviso inválidos con --interval %s: %s", args.interval, e
        )
        return None
    logger.info(
        "Mesas: %s · cada %ss · umbrales T1=%ss T2=%ss R=%ss",
        "detección automática"
        if zones is None
        else ", ".join(t.table_id for t in zones.tables),
        args.interval,
        config.t1,
        config.t2,
        config.reminder,
    )

    if args.classifier in ("claude", "hybrid"):
        # un prompt del .env roto se avisa ahora, no en cada lectura
        provider = os.environ.get("CLAUDE_PROVIDER") or "cli"
        try:
            prompt.check(provider)
        except prompt.PromptError as e:
            logger.error("%s", e)
            return None
        logger.info("IA: %s · prompt %s", provider, prompt.prompt_file(provider))
    try:
        smoother = TableSmoother()  # valida SMOOTH_* del .env antes de arrancar
    except ValueError as e:
        logger.error("Suavizado: %s", e)
        return None
    logger.info(
        "Cambio de estado: %d lecturas (saltos: %d con confianza ≥ %g)",
        smoother.neighbor_readings,
        smoother.hard_readings,
        smoother.hard_min_confidence,
    )
    classifier = get_classifier(args.classifier, **classifier_kwargs(args))
    restaurant = load_restaurant(args.restaurante)
    finished_after = finished_after_seconds(args, demo_enabled(args.demo))
    logger.info("Plato por terminar → vacío a los %gs", finished_after)
    return Pipeline(
        classifier,
        zones,
        observation_handler(config, args.no_telegram, restaurant),
        finished_after=finished_after,
        tables=tables,
        restaurant=restaurant,
        dish_matcher=DishMatcher() if restaurant is not None else None,
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    setup_logging(args.verbose)
    pipeline = build_pipeline(args)
    if pipeline is None:
        return 2
    readings = 0
    try:
        for frame, ts in open_source(args.source, args.interval, args.speed):
            pipeline.process_frame(frame, ts)
            readings += 1
            if args.limit is not None and readings >= args.limit:
                break
    except KeyboardInterrupt:
        logger.info("Cortado a mano")
    finally:
        pipeline.close()
    logger.info("%d lecturas", readings)
    return 0 if readings else 1


if __name__ == "__main__":
    sys.exit(main())
