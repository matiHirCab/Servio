"""Reglas de avisos y envío a Telegram (Matías). Ver docs/context/05-reglas-alertas.md.

API que usa `main.py`:
- `configure(config, telegram=...)`: umbrales ya resueltos y si se manda a Telegram.
- `process(observation)`: una observación de mesa; actualiza el estado, corre el motor,
  manda, recuerda o edita, y escribe todo en `out/eventos.jsonl` (ver `processor.py`).
"""

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from servio_vision import VISION_DIR, settings
from servio_vision.alerts.config import DEFAULT_INTERVAL, AlertConfig, load_config
from servio_vision.alerts.models import TableObservation
from servio_vision.alerts.notify import get_notifier
from servio_vision.alerts.processor import AlertProcessor
from servio_vision.db import Database
from servio_vision.restaurant import Restaurant

DEFAULT_LOG = VISION_DIR / "out" / "eventos.jsonl"

_processor: AlertProcessor | None = None


def configure(
    config: AlertConfig,
    telegram: bool = True,
    log_path: Path | None = None,
    restaurant: Restaurant | None = None,
    env: Mapping[str, str] | None = None,
    database_url: str | None = None,
    local_id: str | None = None,
) -> AlertProcessor:
    """Arranca un estado nuevo (sin mesas ni avisos abiertos) con estos umbrales.
    `env`: token y grupo de Telegram (los pasa `app.py`); sin él, los de la
    configuración del entorno. `database_url` y `local_id`: la base compartida y
    con qué nombre escribe esta casa (A-6); sin URL, SQLite junto al log."""
    global _processor
    if _processor is not None:
        _processor.close()
    log_path = log_path or DEFAULT_LOG
    # con datos del restaurante, todo queda también en la base (junto al log)
    db = None
    if restaurant is not None:
        db = Database(
            log_path.parent / "servio.db",
            url=database_url,
            local_id=local_id or settings.current().local_id,
        )
    _processor = AlertProcessor(
        config, get_notifier(telegram, env), log_path, restaurant, db
    )
    return _processor


def process(observation: TableObservation) -> list[dict[str, Any]]:
    """Procesa una observación. Sin `configure()`, usa producción y Telegram."""
    if _processor is None:
        configure(load_config(DEFAULT_INTERVAL, demo=False))
    assert _processor is not None
    return _processor.process(observation)
