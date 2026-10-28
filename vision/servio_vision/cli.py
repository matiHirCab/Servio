"""Flags de la línea de comandos → `Settings` (A-1).

Los flags solo **pisan** valores: todos arrancan en `None` y lo que no se pasa sale
del `servio.toml` (`--config`), del `.env` o de los defaults de `settings.py`.
`main.py` y `tools/vista_demo.py` comparten este parser y `load_settings`.
"""

import argparse
import logging
from pathlib import Path

from servio_vision.classifier import available_classifiers
from servio_vision.settings import Settings, activate

logger = logging.getLogger("servio")


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
    parser.add_argument(
        "--config",
        type=Path,
        help="servio.toml de la casa (cámara, mesas, umbrales, proveedor…)",
    )
    parser.add_argument("--source", help="video, carpeta, foto, 0 o URL")
    parser.add_argument(
        "--classifier", choices=available_classifiers(), help="por defecto fake"
    )
    parser.add_argument(
        "--zones",
        type=Path,
        help="zonas de mesas a mano (zones.json); sin esto, las mesas se detectan solas",
    )
    parser.add_argument(
        "--interval",
        type=interval_seconds,
        help="segundos entre lecturas (tiempo del video, mínimo 1; por defecto 30)",
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
    parser.add_argument("--fake-mode", choices=["script", "replay"])
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


def add_record_flag(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    """`--record DIR` del loop. Aparte de `build_parser` porque `vista_demo` usa el
    mismo parser con su propio `--record` (la vista en un .mp4, `dest=record_video`)."""
    parser.add_argument(
        "--record",
        type=Path,
        help="guardar cada frame procesado (caras difuminadas) en esta carpeta; "
        "sin esto no se guarda ninguna imagen",
    )
    return parser


def settings_from_args(args: argparse.Namespace) -> Settings:
    """`Settings` con los flags por encima del toml y del entorno. Un `store_true`
    que no se pasó no pisa nada (`--demo` no apaga un `demo = true` del toml)."""
    return Settings.load(
        args.config,
        source=args.source,
        classifier=args.classifier,
        zones=args.zones,
        interval=args.interval,
        speed=args.speed,
        demo=True if args.demo else None,
        telegram=False if args.no_telegram else None,
        fake_mode=args.fake_mode,
        fixture=args.fixture,
        finished_after=args.finished_after,
        restaurante=args.restaurante,
        yolo_conf=args.yolo_conf,
        record=getattr(args, "record", None),  # solo main.py lo agrega
    )


def load_settings(args: argparse.Namespace) -> Settings | None:
    """La configuración para arrancar, ya activa para todo el proceso; `None` (con
    el error logueado) si el toml no existe o está mal, un valor está fuera de rango
    o no hay fuente."""
    try:
        settings = settings_from_args(args)
    except (OSError, ValueError) as e:
        logger.error("Configuración: %s", e)
        return None
    if settings.source is None:
        logger.error("Falta la fuente: --source, o `source` en el toml de --config")
        return None
    return activate(settings)
