"""Armado de la app (A-2): de la configuración a un `Pipeline` andando.

`main.py` parsea flags y llama acá; `tools/vista_demo.py` también arma su pipeline
con `build_pipeline`. Nada de esto lee el entorno: todo viene en `Settings`.

    settings = Settings.load("config/casa.toml")
    pipeline = build_pipeline(settings)
    run(settings)
"""

import logging
import sys
import time
import warnings
from pathlib import Path
from typing import Any

import cv2

from servio_vision import alerts, framing, llm, privacy, prompt, recording
from servio_vision.alerts.config import load_config
from servio_vision.alerts.notify import TelegramNotifier
from servio_vision.capture import (
    CAMERA_RETRY_MAX_S,
    CAMERA_RETRY_S,
    CameraUnavailable,
    is_live_source,
    open_source,
    retry_waits,
)
from servio_vision.classifier import ClassifierUnavailable, get_classifier
from servio_vision.cli import require_source
from servio_vision.dish_match import DishMatcher
from servio_vision.health import STALL_S, HealthMonitor
from servio_vision.pipeline import Pipeline
from servio_vision.privacy import Anonymizer
from servio_vision.recording import FrameRecorder, SessionRecorder
from servio_vision.restaurant import DEFAULT_DIR as RESTAURANT_DIR
from servio_vision.restaurant import Restaurant
from servio_vision.salon import SalonFeed
from servio_vision.salon_web import SalonServer
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
            # un proveedor desconocido falla acá con `UnknownProviderError` (que
            # `build_pipeline` muestra), no con un KeyError de `model_for`
            kwargs.update(
                estimator=llm.make_estimator(cfg=settings),
                reuse_unchanged=settings.claude_reuse_unchanged,
            )
        return kwargs
    if settings.classifier == "claude":
        return {"cfg": settings}  # proveedor, modelo y timeout, como el hybrid
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


def load_tables(settings: Settings) -> tuple[Zones | None, TableTracker | None]:
    """Las mesas: zonas fijas (`zones.json`) o un tracker que las detecta en cada
    lectura. `ImportError` sin ultralytics, `FileNotFoundError` sin el archivo; los
    mensajes ya dicen qué hacer. Lo usan `build_pipeline` y el chequeo de encuadre.
    Una sesión grabada con zonas fijas (B-1) se reproduce con esas mismas zonas."""
    if settings.zones is None:
        recorded = recording.session_zones_arg(settings.source)
        if recorded is not None:
            return recorded, None
        try:
            return None, TableTracker(TableDetector())
        except ImportError as e:
            raise ImportError(
                "Para detectar las mesas solas hace falta ultralytics "
                "(pip install -r requirements-yolo.txt), o pasá --zones config/zones.json."
            ) from e
    if not settings.zones.exists():
        raise FileNotFoundError(
            f"No existe {settings.zones}. Sacá --zones para que las mesas se detecten "
            "solas, o generalo con tools/draw_zones.py --source <video>."
        )
    return load_zones(settings.zones), None


def build_pipeline(settings: Settings) -> Pipeline | None:
    """Zonas, umbrales y clasificador según la configuración. `None` si algo falta
    (ya logueado)."""
    # desde acá, lo que un módulo tome por fallback (`settings.current()`) es esta
    # misma configuración, con lo del toml y los flags
    activate(settings)
    try:
        zones, tables = load_tables(settings)
    except (ImportError, OSError) as e:
        logger.error("%s", e)
        return None
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
    smoothing: dict[str, Any] = {
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
    try:
        classifier = get_classifier(settings.classifier, **classifier_kwargs(settings))
    except (ClassifierUnavailable, NotImplementedError, llm.UnknownProviderError) as e:
        logger.error("%s", e)  # el mensaje dice qué hacer
        return None
    restaurant = load_restaurant(settings.restaurante)
    finished_after = finished_after_seconds(settings)
    logger.info("Plato por terminar → vacío a los %gs", finished_after)
    telegram_env = {
        "TELEGRAM_BOT_TOKEN": settings.telegram_bot_token,
        "TELEGRAM_GROUP_CHAT_ID": settings.telegram_group_chat_id,
    }
    # una cámara en vivo sigue donde quedó tras un reinicio; un video, no
    live = settings.source is not None and is_live_source(settings.source)
    processor = alerts.configure(
        config,
        telegram=settings.telegram,
        restaurant=restaurant,
        env=telegram_env,
        database_url=settings.database_url,
        local_id=settings.local_id,
        resume=live,
    )
    feed = (
        SalonFeed(settings.local_id, processor, restaurant, settings.interval)
        if settings.web_port is not None
        else None
    )
    pipeline = Pipeline(
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
        # la misma base que los avisos: ahí guarda lo que recuerda (sin base, nada)
        state_db=processor.db,
        on_reading=feed.on_reading if feed is not None else None,
    )
    if feed is not None:
        assert settings.web_port is not None
        try:
            server = SalonServer(feed, settings.web_host, settings.web_port).start()
        except OSError as e:
            logger.error(
                "No se pudo abrir la vista del salón en %s:%s (%s). ¿El puerto ya "
                "está en uso? Probá con otro --web",
                settings.web_host,
                settings.web_port,
                e,
            )
            pipeline.close()
            return None
        logger.info("Salón en vivo: %s", server.url)
    if live:
        pipeline.resume()  # los ids y el suavizado, para que sigan siendo los mismos
    return pipeline


def build_health_monitor(settings: Settings) -> HealthMonitor | None:
    """El latido (`health.py`) de una cámara en vivo; un video o una foto no lo
    necesitan. Avisa al chat de `TELEGRAM_ADMIN_CHAT_ID`, nunca al grupo de mozos;
    sin ese chat el aviso queda solo en el log."""
    if not settings.source or not is_live_source(settings.source):
        return None
    token, chat = settings.telegram_bot_token, settings.telegram_admin_chat_id
    if settings.telegram and token and chat:
        admin = TelegramNotifier(token, chat)

        def send(text: str) -> bool:
            return admin.send(text) is not None
    else:
        logger.warning(
            "Sin TELEGRAM_ADMIN_CHAT_ID: si dejan de llegar lecturas, el aviso de "
            "salud solo queda en el log"
        )

        def send(text: str) -> bool:
            return True  # el monitor ya lo logueó; no hay a quién más avisar

    # con una cadencia lenta, 5 min sin lectura es lo normal
    return HealthMonitor(send, stall_s=max(STALL_S, 4 * settings.interval))


def run(settings: Settings, limit: int | None = None) -> int:
    """El loop: abre la fuente, graba si se pidió y procesa cada lectura."""
    if not require_source(settings):
        return 2
    assert settings.source is not None  # lo garantiza `require_source`
    settings = session_settings(settings)  # una sesión grabada: su cadencia
    pipeline = build_pipeline(settings)
    if pipeline is None:
        return 2
    readings = 0
    monitor = build_health_monitor(settings)
    try:
        # La carpeta de grabación primero: si no se puede crear, la cámara todavía
        # no se abrió. Después la fuente, que sale de open_source con las caras
        # difuminadas (C-3): nada de lo que sigue, tampoco la grabación, ve una cara.
        recorder = (
            SessionRecorder(
                settings.record,
                interval=settings.interval,
                local_id=settings.local_id,
                source=settings.source,
                classifier=settings.classifier,
                zones=pipeline.zones if pipeline.tables is None else None,
            )
            if settings.record
            else None
        )
        # El detector de caras de una cámara se arma una sola vez y antes del latido:
        # cargarlo (o bajarlo) en cada reintento, o contarlo como tiempo sin lecturas,
        # no tiene sentido. Con un archivo lo arma `open_source` después de ver que
        # existe, así un error de tipeo no termina en «no se pudo bajar el modelo».
        # Los platos no se pixelan aunque el detector de caras los confunda: los
        # busca el mismo YOLO del clasificador, local, antes de difuminar.
        protect = plate_protector(pipeline)
        anonymize = (
            privacy.default_anonymizer(face_model=settings.face_model, protect=protect)
            if is_live_source(settings.source)
            else None
        )
        if monitor is not None:
            # antes de abrir la cámara: si no abre, también hay que enterarse
            monitor.start()
        frames = _open_frames(settings, anonymize, protect)
        logger.info("Privacidad: caras difuminadas en la captura")
        for frame, ts in frames:
            if recorder is not None:
                recorder = _record(recorder, frame, ts)
            if readings == 0:
                # la luz, antes de gastar la primera clasificación en un cuadro negro
                framing.log_report(frame, None, logger, tables=False)
            pipeline.process_frame(frame, ts)
            if readings == 0:
                # las mesas, ya detectadas: ¿entra entera, con aire, se ve grande?
                found_none = pipeline.tables is not None and not pipeline.tables.tables
                framing.log_report(
                    frame, None if found_none else pipeline.zones, logger, light=False
                )
            readings += 1
            # Un latido es una lectura que llegó a los avisos (ver `frame_ok`).
            if monitor is not None and pipeline.frame_ok:
                monitor.beat()
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
        if monitor is not None:
            monitor.stop()
        pipeline.close()
        if isinstance(recorder, SessionRecorder):
            # con los avisos ya escritos (close los baja al log): la sesión queda
            # completa, con sus eventos, aunque se haya cortado a mano
            recorder.finish(alerts.DEFAULT_LOG)
    logger.info("%d lecturas", readings)
    return 0 if readings else 1


def plate_protector(pipeline: Pipeline) -> privacy.Protector | None:
    """Las cajas de los platos de un frame entero (BGR), con el detector de platos
    del clasificador (yolo o hybrid), para que el difuminado de caras no las tape.
    `None` si el clasificador no tiene detector (fake, claude)."""
    detector = getattr(pipeline.classifier, "detector", None)
    if detector is None:
        return None

    def plates(frame) -> list[privacy.Box]:
        dets = detector.detect(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        return [
            (int(x0), int(y0), int(x1 - x0) + 1, int(y1 - y0) + 1)
            for x0, y0, x1, y1 in (d.box for d in dets)
        ]

    return plates


def _open_frames(
    settings: Settings,
    anonymize: Anonymizer | None,
    protect: privacy.Protector | None = None,
):
    """La fuente de frames. Una cámara que no abre se reintenta con esperas que
    crecen, sin rendirse: tras un corte de luz el router o el celular pueden
    arrancar después que la PC. Cualquier otro error de apertura sale tal cual."""
    source = settings.source
    assert source is not None  # `run` ya lo verificó con `require_source`
    waits = retry_waits(CAMERA_RETRY_S, CAMERA_RETRY_MAX_S)
    while True:
        try:
            return open_source(
                source,
                settings.interval,
                settings.speed,
                anonymize=anonymize,
                face_model=settings.face_model,
                protect=protect,
            )
        except CameraUnavailable as error:
            wait = next(waits)
            logger.warning("%s: reintento en %g s", error, wait)
            time.sleep(wait)


def session_settings(settings: Settings) -> Settings:
    """Si la fuente es una sesión grabada (B-1), la cadencia es la grabada: con otra,
    los umbrales de los avisos no coinciden y la reproducción no reproduce nada."""
    if settings.source is None or not recording.is_session(Path(settings.source)):
        return settings
    info = recording.read_info(Path(settings.source))
    interval = float(info.get("interval", settings.interval))
    if interval != settings.interval:
        logger.info("Sesión grabada cada %g s: se reproduce con esa cadencia", interval)
    return settings.model_copy(update={"interval": interval})


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
