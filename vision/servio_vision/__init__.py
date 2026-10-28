"""Servio: visión, reglas de avisos y Telegram. Ver docs/context/02-arquitectura.md."""

from pathlib import Path

VISION_DIR = Path(__file__).resolve().parents[1]  # vision/
ENV_FILE = VISION_DIR.parent / ".env"  # el .env del repo (09-convenciones.md)
