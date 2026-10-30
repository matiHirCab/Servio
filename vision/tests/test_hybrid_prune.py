"""`hybrid` se olvida de los platos que ya no están: en un servicio largo no crece sin tope."""

from types import SimpleNamespace

import pytest

from servio_vision.classifiers import claude_plates
from servio_vision.classifiers.claude_plates import PlateEstimate, PlatesReport
from servio_vision.classifiers.hybrid import HybridClassifier
from servio_vision.detector import Detection
from servio_vision.tracking import MAX_MISSING
from tests.test_hybrid_reuse import FakeDetector, FakePeople, table

A = Detection((20, 20, 100, 100), 0.9, "food")
B = Detection((220, 20, 300, 100), 0.9, "food")

# lo que guarda `hybrid` por (mesa, plato)
DICTS = ("hidden", "last_fill", "lost_readings", "references", "readings")


@pytest.fixture
def claude(monkeypatch):
    """El modelo contesta `fake.fills` por plato (en orden): por defecto el primero
    lleno y el segundo vacío. Un plato perdido que lee vacío se toma como el hueco
    donde estaba: no se sigue recordando."""
    fake = SimpleNamespace(fills=[0.9, 0.0])

    def estimate(plates, table_id, **kwargs):
        return PlatesReport(
            plates=[
                PlateEstimate(
                    index=i,
                    fill_ratio=fake.fills[min(i - 1, len(fake.fills) - 1)],
                    confidence=0.9,
                    visible=True,
                )
                for i in range(1, len(plates) + 1)
            ],
            people_present=False,
        )

    monkeypatch.setattr(claude_plates, "estimate_plates", estimate)
    return fake


def make(*dets) -> HybridClassifier:
    return HybridClassifier(
        detector=FakeDetector(*dets),
        person_detector=FakePeople(),
        reuse_unchanged=False,
    )


def run(clf, readings, table_id="T1", previous=None):
    """`readings` lecturas seguidas; devuelve los platos de la última."""
    items = previous or []
    for _ in range(readings):
        report = clf.classify(table(), table_id, items)
        items = report.items if report else []
    return items


def keys(clf, plate_id, table_id="T1"):
    """En cuáles de los diccionarios queda algo de ese plato."""
    return {name for name in DICTS if (table_id, plate_id) in getattr(clf, name)}


def test_a_plate_that_is_gone_for_good_is_forgotten(claude):
    clf = make(A, B)
    items = run(clf, 3)
    assert {"readings", "last_fill", "hidden"} <= keys(clf, "p2")  # B existe
    clf.detector.dets = [A]  # se llevan el plato B
    run(clf, MAX_MISSING + 6, previous=items)
    assert keys(clf, "p2") == set()  # B se olvidó, en todos los diccionarios
    assert {"references", "readings", "last_fill", "hidden"} <= keys(clf, "p1")


def test_a_plate_that_is_only_briefly_lost_keeps_its_state(claude):
    clf = make(A, B)
    items = run(clf, 3)
    clf.detector.dets = [A]  # YOLO deja de ver a B una lectura
    run(clf, 1, previous=items)
    assert {"readings", "last_fill", "hidden"} <= keys(clf, "p2")


def test_pruning_one_table_leaves_the_others_alone(claude):
    clf = make(A, B)
    run(clf, 3, table_id="T1")
    run(clf, 3, table_id="T2")
    clf.detector.dets = [A]
    run(clf, MAX_MISSING + 6, table_id="T1")  # T1 pierde a B y lo olvida
    assert keys(clf, "p2", "T1") == set()
    assert {"readings", "last_fill"} <= keys(clf, "p2", "T2")  # T2 sigue entero


def test_a_lost_plate_the_model_still_sees_is_not_forgotten(claude):
    """Un plato que YOLO perdió pero el modelo sigue viendo se recuerda (hasta
    `LOST_KEEP_MAX` lecturas): su estado es lo que lo sostiene."""
    clf = make(A)
    items = run(clf, 3)
    clf.detector.dets = []  # YOLO no ve nada, el modelo sigue viendo el plato lleno
    run(clf, MAX_MISSING + 3, previous=items)
    assert {"readings", "last_fill", "references"} <= keys(clf, "p1")


def test_memory_does_not_grow_over_many_plates(claude):
    """Cientos de platos por día: lo que guarda `hybrid` queda en los que hay."""
    clf = make(A)
    items = []
    for _ in range(25):  # llega un plato, se queda, y se lo llevan
        claude.fills = [0.9]
        clf.detector.dets = [A]
        items = run(clf, 4, previous=items)
        claude.fills = [0.0]  # el modelo lo lee como el hueco: se da por retirado
        clf.detector.dets = []
        items = run(clf, MAX_MISSING + 4, previous=items)
    assert all(len(getattr(clf, name)) <= 1 for name in DICTS)
