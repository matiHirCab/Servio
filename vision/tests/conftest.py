"""Fixtures comunes a todos los tests."""

import os

import pytest

import main
from servio_vision import alerts
from servio_vision.alerts.notify import ConsoleNotifier

# SERVIO_TEST_TELEGRAM=1 pytest → los tests que pasan por alerts mandan al grupo de verdad
REAL_TELEGRAM = os.getenv("SERVIO_TEST_TELEGRAM", "").strip().lower() in {
    "1",
    "true",
    "yes",
}


@pytest.fixture(autouse=True)
def alerts_sandbox(tmp_path, monkeypatch):
    """Ningún test escribe en el `out/eventos.jsonl` real y, salvo que se pida, no
    manda a Telegram.

    Los tests que corren `main.py` o la vista de demo pasan por `alerts.configure()`.
    Por defecto los avisos salen por consola; con `SERVIO_TEST_TELEGRAM=1` se usa el
    Telegram del `.env` (manda mensajes reales al grupo).
    """
    monkeypatch.setattr(alerts, "DEFAULT_LOG", tmp_path / "eventos.jsonl")
    if not REAL_TELEGRAM:
        monkeypatch.setattr(alerts, "get_notifier", lambda *a, **k: ConsoleNotifier())
        # el .env local (DEMO_MODE, ALERT_*) no tiene que cambiar el resultado de los tests
        monkeypatch.setattr(main, "load_dotenv", lambda *a, **k: False)
        monkeypatch.setattr(alerts, "load_dotenv", lambda *a, **k: False)
    monkeypatch.setattr(alerts, "_processor", None)
    yield
    if alerts._processor is not None:
        alerts._processor.close()
