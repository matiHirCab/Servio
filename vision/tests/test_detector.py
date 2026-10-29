"""Filtros y envoltorio del detector de platos (`servio_vision/detector.py`).

`ultralytics` no se instala en el CI (`requirements-yolo.txt`): se reemplaza por un
módulo falso en `sys.modules`.
"""

import logging
import sys
from types import ModuleType, SimpleNamespace

import pytest

from servio_vision import detector
from servio_vision.detector import Detection, PlateDetector, filter_detections


def det(box, confidence=0.9, label="plate"):
    return Detection(box, confidence, label)


def test_iou_of_identical_and_disjoint_boxes():
    assert detector._iou((0, 0, 10, 10), (0, 0, 10, 10)) == pytest.approx(1.0)
    assert detector._iou((0, 0, 10, 10), (20, 20, 30, 30)) == 0.0


def test_contained_box_is_detected():
    assert detector._contained((2, 2, 4, 4), (0, 0, 10, 10))
    assert not detector._contained((0, 0, 10, 10), (20, 20, 30, 30))


def test_filter_without_detections_returns_empty():
    assert filter_detections([]) == []


def test_filter_drops_duplicates_keeping_the_most_confident():
    plate = det((0, 0, 100, 100), 0.9, "plate")
    same_as_bowl = det((2, 2, 100, 100), 0.6, "bowl")
    kept = filter_detections([same_as_bowl, plate])
    assert kept == [plate]


def test_filter_drops_boxes_much_smaller_than_the_typical_plate():
    big = [det((i * 200, 0, i * 200 + 100, 100)) for i in range(3)]
    tiny = det((1000, 0, 1010, 10), 0.8)
    assert filter_detections([*big, tiny]) == big


def test_filter_keeps_separate_plates():
    a, b = det((0, 0, 100, 100)), det((200, 0, 300, 100))
    assert len(filter_detections([a, b])) == 2


class FakeYolo:
    def __init__(self, weights):
        self.weights = weights

    def predict(self, image, **kwargs):
        self.kwargs = kwargs
        boxes = SimpleNamespace(
            xyxy=SimpleNamespace(tolist=lambda: [[0, 0, 100, 100], [200, 0, 300, 100]]),
            conf=SimpleNamespace(tolist=lambda: [0.9, 0.5]),
            cls=SimpleNamespace(tolist=lambda: [0, 1]),
        )
        return [SimpleNamespace(names={0: "plate", 1: "bowl"}, boxes=boxes)]


class FakeYoloe(FakeYolo):
    def __init__(self, weights):
        super().__init__(weights)
        self.classes = None

    def get_text_pe(self, names):
        return ["pe:" + n for n in names]

    def set_classes(self, names, embeddings):
        self.classes = (names, embeddings)


@pytest.fixture
def fake_ultralytics(monkeypatch):
    module = ModuleType("ultralytics")
    module.YOLO = FakeYolo
    module.YOLOE = FakeYoloe
    monkeypatch.setitem(sys.modules, "ultralytics", module)


def test_without_weights_uses_yoloe_with_plate_prompts(fake_ultralytics):
    plate_detector = PlateDetector(weights=None, device="cpu")
    assert isinstance(plate_detector.model, FakeYoloe)
    assert plate_detector.model.weights == detector.DEFAULT_YOLOE
    assert plate_detector.model.classes[0] == ["plate", "bowl"]


def test_missing_weights_file_warns_and_falls_back(fake_ultralytics, tmp_path, caplog):
    with caplog.at_level(logging.WARNING, logger="servio_vision.detector"):
        plate_detector = PlateDetector(weights=str(tmp_path / "no-existe.pt"))
    assert isinstance(plate_detector.model, FakeYoloe)
    assert "no existe" in caplog.text


def test_existing_weights_load_the_trained_model(fake_ultralytics, tmp_path):
    weights = tmp_path / "best.pt"
    weights.write_bytes(b"x")
    plate_detector = PlateDetector(weights=str(weights), device="cpu")
    assert type(plate_detector.model) is FakeYolo
    assert plate_detector.model.weights == str(weights)


def test_device_defaults_to_cpu_and_detect_forwards_settings(fake_ultralytics):
    plate_detector = PlateDetector(weights=None, conf=0.4, imgsz=640, device="")
    assert plate_detector.device == "cpu"

    result = plate_detector.detect(object())

    assert [d.label for d in result] == ["plate", "bowl"]
    assert result[0].confidence == pytest.approx(0.9)
    assert plate_detector.model.kwargs == {
        "conf": 0.4,
        "imgsz": 640,
        "device": "cpu",
        "verbose": False,
    }
