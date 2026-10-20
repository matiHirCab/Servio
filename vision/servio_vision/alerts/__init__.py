"""Reglas de avisos y envío a Telegram (Matías). Ver docs/context/05-reglas-alertas.md.

API que usa `main.py`:
- `configure(config, telegram=...)`: umbrales ya resueltos y si se manda a Telegram.
- `process(observation)`: una observación de mesa; actualiza el estado, corre el motor,
  manda, recuerda o edita, y escribe todo en `out/eventos.jsonl` (ver `processor.py`).
"""

from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from servio_vision.alerts.config import DEFAULT_INTERVAL, AlertConfig, load_config
from servio_vision.alerts.models import TableObservation
from servio_vision.alerts.notify import get_notifier
from servio_vision.alerts.processor import AlertProcessor
from servio_vision.db import Database
from servio_vision.restaurant import Restaurant

VISION_DIR = Path(__file__).resolve().parents[2]
DEFAULT_LOG = VISION_DIR / "out" / "eventos.jsonl"

_processor: AlertProcessor | None = None


def configure(
    config: AlertConfig,
    telegram: bool = True,
    log_path: Path | None = None,
    restaurant: Restaurant | None = None,
) -> AlertProcessor:
    """Arranca un estado nuevo (sin mesas ni avisos abiertos) con estos umbrales."""
    global _processor
    if _processor is not None:
        _processor.close()
    load_dotenv(VISION_DIR.parent / ".env")  # no pisa variables ya puestas
    log_path = log_path or DEFAULT_LOG
    # con datos del restaurante, todo queda también en la base (junto al log)
    db = Database(log_path.parent / "servio.db") if restaurant is not None else None
    _processor = AlertProcessor(
        config, get_notifier(telegram), log_path, restaurant, db
    )
    return _processor


def process(observation: TableObservation) -> list[dict[str, Any]]:
    """Procesa una observación. Sin `configure()`, usa producción y Telegram."""
    if _processor is None:
        configure(load_config(DEFAULT_INTERVAL, demo=False))
    assert _processor is not None
    return _processor.process(observation)
