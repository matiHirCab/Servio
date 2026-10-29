"""El loop de `app.run` con una cámara en vivo: latido y arranque sin cámara."""

from datetime import timedelta

import pytest

from servio_vision import app
from servio_vision.capture import CameraUnavailable
from servio_vision.classifiers.fake import FakeClassifier
from servio_vision.pipeline import Pipeline
from servio_vision.settings import Settings
from tests.test_main import FRAME, T0, ZONES, FlakyClassifier


class FakeMonitor:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.beats = 0

    def start(self) -> None:
        self.events.append("monitor.start")

    def beat(self) -> None:
        self.beats += 1

    def stop(self) -> None:
        self.events.append("monitor.stop")


@pytest.fixture
def loop(monkeypatch):
    """`app.run` con pipeline, monitor y fuente falsos; devuelve cómo armarlo."""
    events: list[str] = []
    monitor = FakeMonitor(events)

    def arrange(classifier, *, readings=3, camera_down_times=0):
        pipeline = Pipeline(classifier, ZONES, lambda o: None)
        down = {"left": camera_down_times}

        def open_source(*args, **kwargs):
            events.append("open")
            if down["left"] > 0:
                down["left"] -= 1
                raise CameraUnavailable("No se pudo abrir la cámara: 0")
            return ((FRAME, T0 + timedelta(seconds=30 * n)) for n in range(readings))

        monkeypatch.setattr(app, "require_source", lambda s: True)
        monkeypatch.setattr(app, "build_pipeline", lambda s: pipeline)
        monkeypatch.setattr(app, "build_health_monitor", lambda s: monitor)
        monkeypatch.setattr(app, "open_source", open_source)
        sleeps: list[float] = []
        monkeypatch.setattr(app.time, "sleep", sleeps.append)
        return events, monitor, sleeps

    return arrange


def test_heartbeat_beats_when_readings_are_processed(loop):
    _, monitor, _ = loop(FakeClassifier())
    assert app.run(Settings.load(source="0")) == 0
    assert monitor.beats == 3


def test_heartbeat_stays_silent_when_every_reading_fails(loop):
    """El modelo caído descarta todas las mesas: el loop sigue, pero los mozos no
    reciben avisos. Eso no es un latido."""
    _, monitor, _ = loop(FlakyClassifier())
    app.run(Settings.load(source="0"))
    assert monitor.beats == 0


def test_camera_down_at_boot_is_retried_with_the_monitor_already_watching(loop):
    events, monitor, sleeps = loop(FakeClassifier(), camera_down_times=2)
    assert app.run(Settings.load(source="0")) == 0
    assert events[0] == "monitor.start"  # un corte al arrancar también se avisa
    assert events.count("open") == 3
    assert len(sleeps) == 2 and sleeps[1] >= sleeps[0]
    assert monitor.beats == 3
