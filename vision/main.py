"""Loop principal de Servio (JES-6).

Cada `interval` segundos (30 por defecto, tiempo del video):
    frame → caras difuminadas → recorte de cada mesa → classify() → suavizado →
    observación → alerts

Cada observación va a `alerts.process()`, que la escribe en `out/eventos.jsonl`
(junto con los avisos): ese log es lo que reproduce `tools/replay.py`.

La configuración sale de `--config casa.toml`, del `.env` y de los flags, que solo
pisan valores (servio_vision/settings.py). El armado está en servio_vision/app.py
y el pipeline en servio_vision/pipeline.py.

    python main.py --config config/casa.toml
    python main.py --source data/videos/demo.mp4 --classifier fake --no-telegram
    python main.py --source data/videos/demo.mp4 --classifier hybrid --demo --speed 10
"""

import logging
import sys

from servio_vision import alerts  # noqa: F401 — tests y herramientas lo toman de acá
from servio_vision.alerts.config import load_config  # noqa: F401
from servio_vision.app import (  # noqa: F401
    _record,
    build_pipeline,
    observation_handler,
    run,
    setup_logging,
)
from servio_vision.cli import add_record_flag, build_parser, settings_from_args
from servio_vision.pipeline import Pipeline  # noqa: F401

logger = logging.getLogger("servio")


def main(argv: list[str] | None = None) -> int:
    args = add_record_flag(build_parser()).parse_args(argv)
    setup_logging(args.verbose)
    try:
        settings = settings_from_args(args)
    except (
        OSError,
        ValueError,
    ) as e:  # toml que no existe o inválido, valor fuera de rango
        logger.error("Configuración: %s", e)
        return 2
    if settings.source is None:
        logger.error("Falta la fuente: --source, o `source` en el toml de --config")
        return 2
    return run(settings, limit=args.limit)


if __name__ == "__main__":
    sys.exit(main())
