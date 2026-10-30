"""El loop de `app.run` con una cámara en vivo: latido y arranque sin cámara."""

from datetime import timedelta

import pytest

from servio_vision import app
from servio_vision.capture import CameraUnavailable
from servio_vision.classifiers.fake import FakeClassifier
from servio_vision.pipeline import Pipeline
from servio_vision.settings import Settings
from servio_vision.zones import Zones
from tests.test_main import FRAME, T0, ZONES, FlakyClassifier


class FakeMonitor:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.beats = 0
        self.open_kwargs: list[dict] = []

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

    def arrange(classifier=None, *, pipeline=None, readings=3, camera_down_times=0):
        pipeline = pipeline or Pipeline(classifier, ZONES, lambda o: None)
        down = {"left": camera_down_times}

        def open_source(*args, **kwargs):
            events.append("open")
            monitor.open_kwargs.append(kwargs)
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


def test_heartbeat_stays_silent_when_the_alerts_fail_on_every_table(loop):
    """`process_frame` se traga el error de `alerts` y devuelve la observación igual:
    los mozos no se enteran de nada, y eso no es un latido."""

    def broken(observation):
        raise RuntimeError("alerts se cayó")

    _, monitor, _ = loop(pipeline=Pipeline(FakeClassifier(), ZONES, broken))
    app.run(Settings.load(source="0"))
    assert monitor.beats == 0


class _Tracker:
    """Detector de mesas: encuentra `tables` o falla si `fails`."""

    def __init__(self, fails: bool) -> None:
        self.fails = fails
        self.tables: list = []

    def update(self, frame):
        if self.fails:
            raise RuntimeError("YOLO se cayó")
        return Zones(frame_size=(200, 100), tables=[])


def test_heartbeat_stays_silent_when_table_detection_keeps_failing(loop):
    pipeline = Pipeline(FakeClassifier(), None, lambda o: None, tables=_Tracker(True))
    _, monitor, _ = loop(pipeline=pipeline)
    app.run(Settings.load(source="0"))
    assert monitor.beats == 0


def test_heartbeat_beats_in_an_empty_room(loop):
    """Sin mesas detectadas no hay nada que avisar: es una sala vacía, no una falla."""
    pipeline = Pipeline(FakeClassifier(), None, lambda o: None, tables=_Tracker(False))
    _, monitor, _ = loop(pipeline=pipeline)
    app.run(Settings.load(source="0"))
    assert monitor.beats == 3


def test_face_detector_is_built_once_and_before_the_monitor_starts(loop, monkeypatch):
    """Cargar (o bajar) el detector de caras en cada reintento de la cámara, o
    contarlo como tiempo sin lecturas, no tiene sentido."""
    events, monitor, _ = loop(FakeClassifier(), camera_down_times=2)
    built: list[dict] = []

    def default_anonymizer(**kwargs):
        built.append(kwargs)
        events.append("anonymizer")
        return lambda frame: frame

    monkeypatch.setattr(app.privacy, "default_anonymizer", default_anonymizer)
    app.run(Settings.load(source="0"))
    assert len(built) == 1
    assert events.index("anonymizer") < events.index("monitor.start")
    assert len(monitor.open_kwargs) == 3
    assert all(kw.get("anonymize") is not None for kw in monitor.open_kwargs)


def test_camera_down_at_boot_is_retried_with_the_monitor_already_watching(loop):
    events, monitor, sleeps = loop(FakeClassifier(), camera_down_times=2)
    assert app.run(Settings.load(source="0")) == 0
    assert events[0] == "monitor.start"  # un corte al arrancar también se avisa
    assert events.count("open") == 3
    assert len(sleeps) == 2 and sleeps[1] >= sleeps[0]
    assert monitor.beats == 3
