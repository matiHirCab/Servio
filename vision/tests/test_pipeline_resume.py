"""Un reinicio no cambia los ids ni la memoria del pipeline: tracker, suavizado,
mesas, platos del pedido y clasificador siguen donde estaban."""

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import numpy as np
import pytest

from servio_vision.classifiers.hybrid import HybridClassifier
from servio_vision.db import Database
from servio_vision.detector import Detection
from servio_vision.llm import PlateEstimate, PlatesReport
from servio_vision.pipeline import Pipeline
from servio_vision.restaurant import Restaurant
from servio_vision.schemas import Item
from servio_vision.smoothing import TableSmoother
from servio_vision.tables import TableTracker
from servio_vision.tracking import IdCounter
from servio_vision.zones import Zones
from tests.model_fakes import patch_estimator
from tests.test_hybrid_reuse import FakeDetector, FakePeople
from tests.test_main import FRAME, T0, ZONES


def roundtrip(state):
    """Lo que pasa por la base: JSON y de vuelta."""
    return json.loads(json.dumps(state))


# --- IdCounter ---------------------------------------------------------------

BOX1, BOX2, BOX3 = (0.1, 0.1, 0.2, 0.2), (0.4, 0.1, 0.2, 0.2), (0.7, 0.1, 0.2, 0.2)


def item(pid, box):
    return Item(
        id=pid,
        position="centro",
        bbox=box,
        state="lleno",
        fill_ratio=0.9,
        confidence=0.9,
    )


def test_id_counter_keeps_the_ids_across_a_restart():
    counter = IdCounter()
    assert counter.assign("T1", [BOX1, BOX2, BOX3], []) == ["p1", "p2", "p3"]
    previous = [item("p2", BOX2), item("p3", BOX3)]
    assert counter.assign("T1", [BOX2, BOX3], previous) == ["p2", "p3"]  # se fue p1

    restarted = IdCounter()
    restarted.restore_state(roundtrip(counter.export_state()))
    # sin lectura anterior: los reconoce por dónde estaban
    assert restarted.assign("T1", [BOX2, BOX3], []) == ["p2", "p3"]
    # y el número sigue subiendo: un plato nuevo, en otro lugar, no reutiliza el de p1
    far = (0.4, 0.7, 0.2, 0.2)
    assert restarted.assign("T1", [BOX2, BOX3, far], []) == ["p2", "p3", "p4"]
    # de no guardarlo, los ids se corren
    assert IdCounter().assign("T1", [BOX2, BOX3], []) == ["p1", "p2"]


# --- TableSmoother -----------------------------------------------------------


def reading(state, fill, confidence=0.9, visible=True):
    return Item(
        id="p1",
        position="centro",
        bbox=BOX1,
        state=state,
        fill_ratio=fill,
        confidence=confidence,
        visible=visible,
    )


def test_smoother_keeps_its_context_across_a_restart():
    smoother = TableSmoother(None, readings=2, hard_readings=3, min_confidence=0.8)
    smoother.update([reading("lleno", 0.9)])
    smoother.update([reading("vacio", 0.0)])  # una lectura de un salto: sin confirmar
    state = roundtrip(smoother.export_state())

    restarted = TableSmoother(None, readings=2, hard_readings=3, min_confidence=0.8)
    restarted.restore_state(state)
    (out,) = restarted.update(
        [reading("vacio", 0.0)]
    )  # la segunda: sigue sin confirmar
    assert out.state == "lleno"
    (out,) = restarted.update([reading("vacio", 0.0)])  # la tercera: confirma
    assert out.state == "vacio"
    # sin guardar el suavizado, una sola lectura aceptaría el salto
    cold = TableSmoother(None, readings=2, hard_readings=3, min_confidence=0.8)
    assert cold.update([reading("vacio", 0.0)])[0].state == "vacio"


def test_smoother_keeps_the_time_a_plate_has_been_in_its_state():
    smoother = TableSmoother(600)
    t0 = datetime(2026, 10, 30, 20, 0, tzinfo=UTC)
    smoother.update([reading("por_terminar", 0.2)], t0)
    restarted = TableSmoother(600)
    restarted.restore_state(roundtrip(smoother.export_state()))
    (out,) = restarted.update(
        [reading("por_terminar", 0.2)], t0 + timedelta(seconds=601)
    )
    assert (
        out.state == "vacio"
    )  # pasaron 10 min en por_terminar, también con el reinicio


# --- TableTracker ------------------------------------------------------------


class FakeTables:
    def __init__(self, boxes):
        self.boxes = boxes

    def detect(self, frame):
        return [(box, 0.9) for box in self.boxes]


LEFT, RIGHT = (10.0, 10.0, 90.0, 90.0), (210.0, 10.0, 290.0, 90.0)
BLANK = np.zeros((100, 300, 3), np.uint8)


def test_table_ids_survive_a_restart_even_if_a_table_is_hidden():
    tracker = TableTracker(FakeTables([LEFT, RIGHT]))
    tracker.update(BLANK)
    assert [t.table_id for t in tracker.tables] == ["T1", "T2"]

    restarted = TableTracker(FakeTables([RIGHT]))  # tapan la mesa de la izquierda
    restarted.restore_state(roundtrip(tracker.export_state()))
    zones = restarted.update(BLANK)
    assert [t.table_id for t in zones.tables] == [
        "T1",
        "T2",
    ]  # la derecha sigue siendo T2
    # sin guardarlo, la única mesa visible pasa a ser T1
    cold = TableTracker(FakeTables([RIGHT]))
    assert [t.table_id for t in cold.update(BLANK).tables] == ["T1"]


# --- Restaurant --------------------------------------------------------------


def test_dish_assignments_survive_a_restart():
    restaurant = Restaurant.load()
    dish = restaurant.dish("T1", "T1-p1")
    assert dish is not None
    restarted = Restaurant.load()
    restarted.restore_state(roundtrip(restaurant.export_state()))
    assert restarted.is_assigned("T1", "T1-p1")
    assert restarted.dish("T1", "T1-p1") == dish
    assert not Restaurant.load().is_assigned("T1", "T1-p1")


# --- HybridClassifier --------------------------------------------------------


def test_hybrid_keeps_its_small_memory_across_a_restart():
    clf = HybridClassifier(detector=FakeDetector(), person_detector=FakePeople())
    clf.people_present["T1"] = True
    clf.last_fill[("T1", "p2")] = 0.4
    clf.hidden[("T1", "p2")] = 1
    clf.lost_readings[("T1", "p3")] = 2
    clf.ids.assign("T1", [BOX1, BOX2], [])
    restarted = HybridClassifier(detector=FakeDetector(), person_detector=FakePeople())
    restarted.restore_state(roundtrip(clf.export_state()))
    assert restarted.people_present == {"T1": True}
    assert restarted.last_fill == {("T1", "p2"): 0.4}
    assert restarted.hidden == {("T1", "p2"): 1}
    assert restarted.lost_readings == {("T1", "p3"): 2}
    assert restarted.ids.assign("T1", [BOX2], []) == ["p2"]
    # lo que no se guarda empieza de cero: pesa (referencias) o se rearma solo
    assert restarted.references == {} and restarted.readings == {}


# --- El pipeline entero ------------------------------------------------------

X = Detection((5, 5, 35, 35), 0.9, "food")
Y = Detection((40, 5, 70, 35), 0.9, "food")
Z = Detection((5, 45, 35, 75), 0.9, "food")
ONE_TABLE = Zones(frame_size=(200, 100), tables=ZONES.tables[:1])


@pytest.fixture
def full_plates(monkeypatch):
    """El modelo lee todos los platos llenos, salvo los de más allá de `model.full`
    (los que YOLO perdió van al final): esos los lee vacíos, como el hueco de un
    plato que se llevaron."""
    model = SimpleNamespace(full=99)

    def estimate(plates, table_id, **kwargs):
        return PlatesReport(
            plates=[
                PlateEstimate(
                    index=i,
                    fill_ratio=0.9 if i <= model.full else 0.0,
                    confidence=0.9,
                    visible=True,
                )
                for i in range(1, len(plates) + 1)
            ],
            people_present=True,
        )

    patch_estimator(monkeypatch, estimate)
    return model


def make_pipeline(dets, db=None):
    seen = []
    classifier = HybridClassifier(
        detector=FakeDetector(*dets),
        person_detector=FakePeople(),
        reuse_unchanged=False,
    )
    pipeline = Pipeline(classifier, ONE_TABLE, seen.append, state_db=db)
    return pipeline, classifier, seen


def run_frames(pipeline, n, start=0):
    for k in range(start, start + n):
        pipeline.process_frame(FRAME, T0 + timedelta(seconds=30 * k))


def plate_ids(observations):
    return sorted(i.track_id for i in observations[-1].items)


def test_a_restarted_pipeline_keeps_the_plate_ids(tmp_path, full_plates):
    db = Database(tmp_path / "servio.db")
    a, a_clf, a_seen = make_pipeline([X, Y, Z], db)
    run_frames(a, 3)
    assert plate_ids(a_seen) == ["T1-p1", "T1-p2", "T1-p3"]
    a_clf.detector.dets = [Y, Z]  # se llevan el plato de X
    full_plates.full = 2  # el modelo lo lee como el hueco: se da por retirado
    run_frames(a, 7, start=3)  # unas lecturas hasta que X desaparece de la observación
    assert plate_ids(a_seen) == ["T1-p2", "T1-p3"]
    a.save_state()

    b, _, b_seen = make_pipeline([Y, Z], db)
    assert b.resume(max_age_s=600)
    run_frames(b, 1, start=10)
    assert plate_ids(b_seen) == ["T1-p2", "T1-p3"]  # los mismos ids que antes

    cold, _, cold_seen = make_pipeline([Y, Z])  # sin reanudar, los ids se corren
    run_frames(cold, 1, start=10)
    assert plate_ids(cold_seen) == ["T1-p1", "T1-p2"]


def test_a_stale_snapshot_is_ignored(tmp_path, full_plates):
    db = Database(tmp_path / "servio.db")
    a, _, _ = make_pipeline([X, Y], db)
    run_frames(a, 2)
    a.save_state()
    b, _, _ = make_pipeline([X, Y], db)
    assert b.resume(max_age_s=-1) is False


def test_a_damaged_snapshot_starts_cold_without_raising(tmp_path, full_plates):
    db = Database(tmp_path / "servio.db")
    db.save_state("{esto no es JSON", datetime.now(UTC), kind="pipeline")
    b, _, _ = make_pipeline([X, Y], db)
    assert b.resume(max_age_s=600) is False
    db.save_state(json.dumps({"version": 99}), datetime.now(UTC), kind="pipeline")
    assert b.resume(max_age_s=600) is False


def test_without_a_database_resume_does_nothing(full_plates):
    b, _, _ = make_pipeline([X, Y])
    assert b.resume(max_age_s=600) is False


def test_state_is_saved_every_few_seconds_and_when_closing(tmp_path, full_plates):
    db = Database(tmp_path / "servio.db")
    saves = []
    real = db.save_state
    db.save_state = lambda *a, **k: (saves.append(k.get("kind")), real(*a, **k))[1]
    a, _, _ = make_pipeline([X], db)
    run_frames(a, 6)  # seis lecturas seguidas, en menos de 5 s
    assert saves.count("pipeline") == 1
    a.close()
    assert saves.count("pipeline") == 2  # al cerrar guarda lo último


@pytest.mark.parametrize(
    ("source", "resumed"), [("0", True), ("salon.mp4", False)], ids=["camara", "video"]
)
def test_build_pipeline_resumes_only_for_a_live_camera(
    tmp_path, monkeypatch, source, resumed
):
    from servio_vision import alerts, app
    from servio_vision.settings import Settings
    from servio_vision.zones import save_zones

    monkeypatch.setattr(alerts, "_processor", None)
    zones_path = tmp_path / "zones.json"
    save_zones(ZONES, zones_path)
    calls = []

    def resume(self, *args, **kwargs):
        calls.append(self.state_db is not None)  # con la base de los avisos
        return False

    monkeypatch.setattr(Pipeline, "resume", resume)
    settings = Settings.load(
        source=source, classifier="fake", zones=zones_path, telegram=False
    )
    pipeline = app.build_pipeline(settings)
    try:
        assert pipeline is not None
        assert calls == ([True] if resumed else [])
    finally:
        pipeline.close()
        alerts._processor.close()


def test_the_pipeline_and_alert_snapshots_do_not_overwrite_each_other(tmp_path):
    db = Database(tmp_path / "servio.db")
    at = datetime.now(UTC)
    db.save_state('{"a": 1}', at)
    db.save_state('{"p": 1}', at, kind="pipeline")
    assert db.load_state() == ('{"a": 1}', at)
    assert db.load_state(kind="pipeline") == ('{"p": 1}', at)
