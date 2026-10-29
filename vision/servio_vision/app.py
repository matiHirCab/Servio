"""Armado de la app (A-2): de la configuración a un `Pipeline` andando.

`main.py` parsea flags y llama acá; `tools/vista_demo.py` también arma su pipeline
con `build_pipeline`. Nada de esto lee el entorno: todo viene en `Settings`.

    settings = Settings.load("config/casa.toml")
    pipeline = build_pipeline(settings)
    run(settings)
"""

import logging
import sys
import warnings

from servio_vision import alerts, prompt
from servio_vision.alerts.config import load_config
from servio_vision.capture import open_source
from servio_vision.classifier import get_classifier
from servio_vision.cli import require_source
from servio_vision.dish_match import DishMatcher
from servio_vision.pipeline import Pipeline
from servio_vision.recording import FrameRecorder
from servio_vision.restaurant import DEFAULT_DIR as RESTAURANT_DIR
from servio_vision.restaurant import Restaurant
from servio_vision.settings import Settings, activate
from servio_vision.smoothing import TableSmoother
from servio_vision.tables import TableDetector, TableTracker
from servio_vision.zones import Zones, load_zones

logger = logging.getLogger("servio")

# Plato en por_terminar tanto tiempo → vacio (quedan restos). Producción: 6 min.
PROD_FINISHED_AFTER = 6 * 60
DEMO_FINISHED_READINGS = 6  # en demo, 6 lecturas (30 s con --interval 5)


def load_restaurant(directory) -> Restaurant | None:
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


def classifier_kwargs(settings: Settings) -> dict:
    """Lo que cada clasificador recibe de la configuración."""
    if settings.classifier in ("yolo", "hybrid"):
        kwargs: dict = {
            "weights": str(settings.yolo_weights) if settings.yolo_weights else None,
            "device": settings.yolo_device,
        }
        # umbral del detector: con videos de baja calidad YOLOE da confianzas bajas
        if settings.yolo_conf is not None:
            kwargs["conf"] = settings.yolo_conf
        if settings.classifier == "hybrid":
            provider = settings.claude_provider
            kwargs.update(
                provider=provider,
                model=settings.model_for(provider),
                timeout_s=settings.timeout_for(provider),
            )
        return kwargs
    if settings.classifier != "fake":
        return {}
    kwargs = {"mode": settings.fake_mode}
    if settings.fixture:
        kwargs["fixture"] = settings.fixture
    return kwargs


def finished_after_seconds(settings: Settings) -> float:
    """`finished_after` configurado, o 6 lecturas en demo / 6 min en producción."""
    if settings.finished_after is not None:
        return settings.finished_after
    if settings.demo:
        return DEMO_FINISHED_READINGS * settings.interval
    return PROD_FINISHED_AFTER


def build_pipeline(settings: Settings) -> Pipeline | None:
    """Zonas, umbrales y clasificador según la configuración. `None` si algo falta
    (ya logueado)."""
    # desde acá, lo que un módulo tome por fallback (`settings.current()`) es esta
    # misma configuración, con lo del toml y los flags
    activate(settings)
    zones: Zones | None = None
    tables: TableTracker | None = None
    if settings.zones is None:
        try:
            tables = TableTracker(TableDetector())
        except ImportError:
            logger.error(
                "Para detectar las mesas solas hace falta ultralytics "
                "(pip install -r requirements-yolo.txt), o pasá --zones config/zones.json."
            )
            return None
    elif not settings.zones.exists():
        logger.error(
            "No existe %s. Sacá --zones para que las mesas se detecten solas, o "
            "generalo con tools/draw_zones.py --source <video>.",
            settings.zones,
        )
        return None
    else:
        zones = load_zones(settings.zones)
    try:
        # los umbrales ya vienen resueltos (flags > toml > .env): van como "flags"
        config = load_config(
            round(settings.interval),
            settings.demo,
            t1=settings.alert_t1,
            t2=settings.alert_t2,
            reminder=settings.alert_reminder,
            env={},
        )
    except ValueError as e:
        logger.error(
            "Umbrales de aviso inválidos con --interval %s: %s", settings.interval, e
        )
        return None
    logger.info(
        "Mesas: %s · cada %ss · umbrales T1=%ss T2=%ss R=%ss",
        "detección automática"
        if zones is None
        else ", ".join(t.table_id for t in zones.tables),
        settings.interval,
        config.t1,
        config.t2,
        config.reminder,
    )

    if settings.classifier in ("claude", "hybrid"):
        # un prompt roto se avisa ahora, no en cada lectura
        provider = settings.claude_provider
        prompt_file = settings.prompt_file_for(provider)
        try:
            prompt.check(provider, prompt_file)
        except prompt.PromptError as e:
            logger.error("%s", e)
            return None
        logger.info("IA: %s · prompt %s", provider, prompt_file)
    smoothing = {
        "readings": settings.smooth_readings,
        "hard_readings": settings.smooth_hard_readings,
        "min_confidence": settings.smooth_min_confidence,
    }
    logger.info(
        "Cambio de estado: %d lecturas (saltos: %d con confianza ≥ %g)",
        smoothing["readings"],
        smoothing["hard_readings"],
        smoothing["min_confidence"],
    )
    classifier = get_classifier(settings.classifier, **classifier_kwargs(settings))
    restaurant = load_restaurant(settings.restaurante)
    finished_after = finished_after_seconds(settings)
    logger.info("Plato por terminar → vacío a los %gs", finished_after)
    telegram_env = {
        "TELEGRAM_BOT_TOKEN": settings.telegram_bot_token,
        "TELEGRAM_GROUP_CHAT_ID": settings.telegram_group_chat_id,
    }
    alerts.configure(
        config,
        telegram=settings.telegram,
        restaurant=restaurant,
        env=telegram_env,
        database_url=settings.database_url,
        local_id=settings.local_id,
    )
    return Pipeline(
        classifier,
        zones,
        alerts.process,
        finished_after=finished_after,
        tables=tables,
        restaurant=restaurant,
        dish_matcher=DishMatcher(
            model=settings.api_model(), api_key=settings.anthropic_api_key
        )
        if restaurant is not None
        else None,
        smoother_factory=lambda: TableSmoother(finished_after, **smoothing),
    )


def run(settings: Settings, limit: int | None = None) -> int:
    """El loop: abre la fuente, graba si se pidió y procesa cada lectura."""
    if not require_source(settings):
        return 2
    pipeline = build_pipeline(settings)
    if pipeline is None:
        return 2
    readings = 0
    try:
        # La carpeta de grabación primero: si no se puede crear, la cámara todavía
        # no se abrió. Después la fuente, que sale de open_source con las caras
        # difuminadas (C-3): nada de lo que sigue, tampoco la grabación, ve una cara.
        recorder = FrameRecorder(settings.record) if settings.record else None
        frames = open_source(
            settings.source,
            settings.interval,
            settings.speed,
            face_model=settings.face_model,
        )
        logger.info("Privacidad: caras difuminadas en la captura")
        for frame, ts in frames:
            if recorder is not None:
                recorder = _record(recorder, frame, ts)
            pipeline.process_frame(frame, ts)
            readings += 1
            if limit is not None and readings >= limit:
                break
    except OSError as e:
        # --record que no se puede crear, fuente que no existe o no abre, modelo de
        # caras que falta o falla (PrivacyError): sin frames no hay nada que hacer
        logger.error("%s", e)
        return 2
    except KeyboardInterrupt:
        logger.info("Cortado a mano")
    finally:
        pipeline.close()
    logger.info("%d lecturas", readings)
    return 0 if readings else 1


def _record(recorder: FrameRecorder, frame, ts) -> FrameRecorder | None:
    """Guarda el frame; si el disco o el share fallan, avisa y deja de grabar. Los
    avisos a los mozos siguen: grabar es auxiliar."""
    try:
        recorder.save(frame, ts)
        return recorder
    except OSError as e:
        logger.error("No se pudo grabar el frame, sigo sin grabar: %s", e)
        return None


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
