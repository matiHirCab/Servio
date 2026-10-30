"""Detector de personas y pixelado de recortes (`servio_vision/people.py`)."""

import logging
from types import SimpleNamespace

import numpy as np
from PIL import Image

from servio_vision import people
from servio_vision.people import YoloPersonDetector, anonymize_crop

# conftest reemplaza `people.load_detector` en cada test: se guarda la función real
_original_load_detector = people.load_detector


def noisy(size=(100, 100)):
    rng = np.random.default_rng(1)
    return Image.fromarray(rng.integers(0, 255, (size[1], size[0], 3), np.uint8))


def test_no_people_returns_the_same_crop():
    crop = noisy()
    assert anonymize_crop(crop, (0, 0), []) is crop


def test_person_outside_the_crop_returns_the_same_crop():
    crop = noisy()
    assert anonymize_crop(crop, (0, 0), [(500, 500, 600, 600)]) is crop


def test_person_inside_the_crop_is_pixelated_and_the_rest_is_untouched():
    crop = noisy()
    result = anonymize_crop(crop, (0, 0), [(20, 20, 60, 60)])
    before, after = np.asarray(crop), np.asarray(result)
    assert not np.array_equal(before[30:50, 30:50], after[30:50, 30:50])
    assert np.array_equal(before[80:, 80:], after[80:, 80:])
    # el original no se toca
    assert np.array_equal(before, np.asarray(crop))


def test_crop_origin_shifts_the_person_box():
    crop = noisy()
    # la persona está en (200..240) de la mesa; el recorte empieza en x=190
    result = anonymize_crop(crop, (190, 0), [(200, 20, 240, 60)])
    assert not np.array_equal(np.asarray(crop), np.asarray(result))


def test_yolo_detector_asks_only_for_persons(monkeypatch):
    calls = {}

    class Model:
        def predict(self, img, **kwargs):
            calls.update(kwargs)
            boxes = SimpleNamespace(xyxy=SimpleNamespace(tolist=lambda: [[1, 2, 3, 4]]))
            return [SimpleNamespace(boxes=boxes)]

    monkeypatch.setattr(people, "coco_model", lambda weights: Model())
    detector = YoloPersonDetector()

    assert detector.detect(noisy()) == [(1, 2, 3, 4)]
    assert calls["classes"] == [people.COCO_PERSON]
    assert calls["conf"] == people.MIN_CONF
    assert calls["imgsz"] == people.IMAGE_SIZE


def test_load_detector_returns_none_with_a_warning_when_it_cannot_load(
    monkeypatch, caplog
):
    def broken(weights):
        raise ImportError("sin ultralytics")

    monkeypatch.setattr(people, "coco_model", broken)
    with caplog.at_level(logging.WARNING, logger="servio_vision.people"):
        result = _original_load_detector()
    assert result is None
    assert "Sin detector de personas" in caplog.text
