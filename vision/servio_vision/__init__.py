"""Servio: visión, reglas de avisos y Telegram. Ver docs/context/02-arquitectura.md."""

import sys
from pathlib import Path

VISION_DIR = Path(__file__).resolve().parents[1]  # vision/
ENV_FILE = VISION_DIR.parent / ".env"  # el .env del repo (09-convenciones.md)
DEFAULT_LOCAL = "local"  # local_id de una casa que no dice el suyo (db.py, settings.py)


def utf8_console() -> None:
    """La consola de Windows (cp1252) no puede escribir los emojis de los avisos ni
    las flechas del tablero: sin esto, imprimirlos tira UnicodeEncodeError. Acá, y no
    en `app.py`, para que una herramienta chica no cargue el pipeline entero."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
