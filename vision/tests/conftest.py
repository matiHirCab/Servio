"""Fixtures comunes a todos los tests."""

import os

import pytest

from servio_vision import alerts, people, privacy, settings
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
        # el .env local (DEMO_MODE, ALERT_*…) no tiene que cambiar el resultado de los
        # tests: la configuración se arma solo del entorno del proceso
        monkeypatch.setattr(settings, "ENV_FILE", None)
    # ninguna configuración activa de un test anterior (cli.load_settings la deja)
    monkeypatch.setattr(settings, "_active", None)
    monkeypatch.setattr(alerts, "_processor", None)
    yield
    if alerts._processor is not None:
        alerts._processor.close()


class NoFaces:
    """Detector de caras que no ve ninguna: los tests no bajan el modelo de YuNet."""

    def detect(self, frame):
        return []


@pytest.fixture(autouse=True)
def no_face_model(monkeypatch):
    """`capture.open_source` y `CameraReader` difuminan caras por defecto (C-3); sin
    esto cada test que lee un video intentaría bajar el detector. Un test que quiera
    ver caras pixeladas pone su propio detector con `monkeypatch`."""
    monkeypatch.setattr(privacy, "load_detector", lambda *a, **k: NoFaces())


@pytest.fixture(autouse=True)
def no_person_model(monkeypatch):
    """`HybridClassifier()` arma un detector de personas (YOLO) si puede; en los
    tests, ninguno: `people_present` queda como lo diga el Claude falso. Un test que
    quiera personas pasa `person_detector=`."""
    monkeypatch.setattr(people, "load_detector", lambda *a, **k: None)
