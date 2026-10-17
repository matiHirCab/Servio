"""Tests de los clasificadores `yolo` e `hybrid` con detector y Claude falsos (TOM-5)."""

import io
from types import SimpleNamespace

import pytest
from PIL import Image

from servio_vision.classifiers import claude_plates, hybrid
from servio_vision.classifiers.claude_plates import PlateEstimate, PlatesReport
from servio_vision.classifiers.hybrid import HybridClassifier
from servio_vision.classifiers.yolo import YoloClassifier
from servio_vision.detector import Detection


def _jpeg(w=400, h=200):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), "white").save(buf, "JPEG")
    return buf.getvalue()


class FakeDetector:
    def __init__(self, dets):
        self.dets = dets

    def detect(self, img):
        return self.dets


PLATES = [
    Detection((220, 20, 300, 100), 0.8, "food"),
    Detection((20, 20, 100, 100), 0.9, "finished_food"),
]


@pytest.fixture
def claude(monkeypatch):
    """Claude falso: devuelve `fake.answer` (None = Claude falló) y guarda los recortes."""
    fake = SimpleNamespace(answer=None, calls=[])

    def estimate(table, plates, table_id, **kwargs):
        fake.calls.append(plates)
        return fake.answer

    monkeypatch.setattr(claude_plates, "estimate_plates", estimate)
    return fake


def test_hybrid_uses_claude_fill_ratio(claude):
    claude.answer = PlatesReport(
        plates=[
            PlateEstimate(index=1, fill_ratio=0.02, confidence=0.9, visible=True),
            PlateEstimate(index=2, fill_ratio=0.5, confidence=0.8, visible=True),
        ],
        people_present=False,
    )
    report = HybridClassifier(detector=FakeDetector(PLATES)).classify(_jpeg(), "T1", [])
    # ordenados de izquierda a derecha: el de x=20 es p2 (segundo detectado)
    assert [(i.id, i.state) for i in report.items] == [
        ("p2", "vacio"),
        ("p1", "comiendo"),
    ]
    assert report.people_present is False
    assert report.items[0].bbox == pytest.approx((0.05, 0.1, 0.2, 0.4))
    assert len(claude.calls[0]) == 2


def test_hybrid_hidden_plate_has_no_state(claude):
    claude.answer = PlatesReport(
        plates=[PlateEstimate(index=1, fill_ratio=0.9, confidence=0.3, visible=False)]
    )
    det = FakeDetector([Detection((20, 20, 100, 100), 0.9, "plate")])
    report = HybridClassifier(detector=det).classify(_jpeg(), "T1", [])
    assert report.items[0].visible is False
    assert report.items[0].state is None


def test_hybrid_falls_back_to_yolo_labels(claude):
    report = HybridClassifier(detector=FakeDetector(PLATES)).classify(_jpeg(), "T1", [])
    assert [i.state for i in report.items] == ["vacio", "lleno"]
    assert report.items[0].confidence == pytest.approx(0.45)


def test_hybrid_without_claude_or_trained_yolo_is_discarded(claude):
    det = FakeDetector([Detection((20, 20, 100, 100), 0.9, "plate")])
    assert HybridClassifier(detector=det).classify(_jpeg(), "T1", []) is None


def test_hybrid_no_plates_skips_claude(claude):
    report = HybridClassifier(detector=FakeDetector([])).classify(_jpeg(), "T1", [])
    assert report.items == []
    assert claude.calls == []


def test_hybrid_keeps_ids_between_readings(claude):
    clf = HybridClassifier(detector=FakeDetector(PLATES))
    first = clf.classify(_jpeg(), "T1", [])
    clf.detector = FakeDetector([PLATES[0]])
    second = clf.classify(_jpeg(), "T1", first.items)
    assert [i.id for i in second.items] == ["p1"]


def test_crop_has_margin():
    img = Image.new("RGB", (400, 200))
    crop = hybrid._crop(img, (0.25, 0.25, 0.5, 0.5))
    assert crop.size == (240, 120)


def test_yolo_classifier_states_from_labels():
    report = YoloClassifier(detector=FakeDetector(PLATES)).classify(_jpeg(), "T1", [])
    assert [i.state for i in report.items] == ["vacio", "lleno"]


def test_yolo_classifier_without_trained_weights_returns_none():
    det = FakeDetector([Detection((20, 20, 100, 100), 0.9, "plate")])
    assert YoloClassifier(detector=det).classify(_jpeg(), "T1", []) is None
