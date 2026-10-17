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
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import numpy as np

from servio_vision import alerts
from servio_vision.alerts.config import DEFAULT_INTERVAL, AlertConfig, load_config
from servio_vision.alerts.models import TableObservation
from servio_vision.capture import open_source
from servio_vision.classifier import Classifier, available_classifiers, get_classifier
from servio_vision.schemas import Item, TableReport
from servio_vision.smoothing import TableSmoother
from servio_vision.zones import DEFAULT_ZONES, Zones, crop_table, load_zones, to_jpeg

logger = logging.getLogger("servio")

ObservationHandler = Callable[[TableObservation], object]


class Pipeline:
    """Una lectura = un frame: clasifica y suaviza cada mesa y arma su observación."""

    def __init__(
        self,
        classifier: Classifier,
        zones: Zones,
        on_observation: ObservationHandler,
    ) -> None:
        self.classifier = classifier
        self.zones = zones
        self.on_observation = on_observation
        self.smoothers = {t.table_id: TableSmoother() for t in zones.tables}
        self.previous: dict[str, list[Item]] = {t.table_id: [] for t in zones.tables}
        self._pool = ThreadPoolExecutor(max_workers=max(1, len(zones.tables)))

    def process_frame(self, frame: np.ndarray, ts: datetime) -> list[TableObservation]:
        """Procesa un frame. Un error en una mesa descarta esa mesa, no corta el loop."""
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
                items = self.smoothers[table_id].update(report.items)
                self.previous[table_id] = items
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


def observation_handler(config: AlertConfig, no_telegram: bool) -> ObservationHandler:
    """`alerts.process`, ya configurado con los umbrales resueltos (`--interval`,
    `--demo`) y con Telegram o consola según `--no-telegram`."""
    alerts.configure(config, telegram=not no_telegram)
    return alerts.process


def classifier_kwargs(args: argparse.Namespace) -> dict:
    if args.classifier != "fake":
        return {}
    kwargs: dict = {"mode": args.fake_mode}
    if args.fixture:
        kwargs["fixture"] = args.fixture
    return kwargs


def demo_enabled(flag: bool) -> bool:
    """--demo le gana a DEMO_MODE (09-convenciones.md)."""
    return flag or os.getenv("DEMO_MODE", "").strip().lower() in {"1", "true", "yes"}


def build_parser(
    description: str = "Servio: loop principal.",
) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--source", required=True, help="video, carpeta, 0 o URL")
    parser.add_argument("--classifier", default="fake", choices=available_classifiers())
    parser.add_argument("--zones", type=Path, default=DEFAULT_ZONES)
    parser.add_argument(
        "--interval",
        type=float,
        default=DEFAULT_INTERVAL,
        help="segundos entre lecturas (tiempo del video)",
    )
    parser.add_argument(
        "--speed", type=float, help="reproducir el video a esta velocidad"
    )
    parser.add_argument("--demo", action="store_true", help="umbrales de demo")
    parser.add_argument(
        "--no-telegram", action="store_true", help="no mandar a Telegram"
    )
    parser.add_argument("--limit", type=int, help="cortar tras N lecturas")
    parser.add_argument("--fake-mode", default="script", choices=["script", "replay"])
    parser.add_argument("--fixture", type=Path, help="jsonl para --fake-mode replay")
    return parser


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        stream=sys.stderr,
    )


def build_pipeline(args: argparse.Namespace) -> Pipeline | None:
    """Zonas, umbrales y clasificador según los flags. `None` si algo falta (ya logueado)."""
    if not args.zones.exists():
        logger.error(
            "No existe %s. Generalo con la detección de mesas o con "
            "tools/draw_zones.py --source <video>.",
            args.zones,
        )
        return None
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
        ", ".join(t.table_id for t in zones.tables),
        args.interval,
        config.t1,
        config.t2,
        config.reminder,
    )

    classifier = get_classifier(args.classifier, **classifier_kwargs(args))
    return Pipeline(classifier, zones, observation_handler(config, args.no_telegram))


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    setup_logging()
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
