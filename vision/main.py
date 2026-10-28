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

import sys

from servio_vision.app import run, setup_logging
from servio_vision.cli import add_record_flag, build_parser, load_settings


def main(argv: list[str] | None = None) -> int:
    args = add_record_flag(build_parser()).parse_args(argv)
    setup_logging(args.verbose)
    settings = load_settings(args)
    if settings is None:
        return 2
    return run(settings, limit=args.limit)


if __name__ == "__main__":
    sys.exit(main())
