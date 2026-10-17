"""Fixtures comunes a todos los tests."""

import pytest

from servio_vision import alerts
from servio_vision.alerts.notify import ConsoleNotifier


@pytest.fixture(autouse=True)
def alerts_sandbox(tmp_path, monkeypatch):
    """Ningún test escribe en el `out/eventos.jsonl` real ni manda a Telegram.

    Los tests que corren `main.py` o la vista de demo pasan por `alerts.configure()`:
    sin esto, con un `.env` con credenciales mandarían mensajes reales al grupo.
    """
    monkeypatch.setattr(alerts, "DEFAULT_LOG", tmp_path / "eventos.jsonl")
    monkeypatch.setattr(alerts, "get_notifier", lambda *a, **k: ConsoleNotifier())
    monkeypatch.setattr(alerts, "_processor", None)
    yield
    if alerts._processor is not None:
        alerts._processor.close()
