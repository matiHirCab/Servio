"""Tests de detector.py sin ultralytics: el `imgsz` se ajusta al tamaño de la imagen."""

from types import SimpleNamespace

import numpy as np
from PIL import Image

from servio_vision.detector import PlateDetector, fit_imgsz


def test_fit_imgsz_does_not_upscale():
    assert fit_imgsz(350, 245, 1280) == 352  # recorte de mesa de un video 640×480
    assert fit_imgsz(640, 480, 1280) == 640
    assert fit_imgsz(2000, 1500, 1280) == 1280  # las grandes quedan en el tope
    assert fit_imgsz(10, 10, 1280) == 32


class FakeModel:
    def __init__(self):
        self.kwargs = []

    def predict(self, image, **kwargs):
        self.kwargs.append(kwargs)
        boxes = SimpleNamespace(
            xyxy=SimpleNamespace(tolist=list),
            conf=SimpleNamespace(tolist=list),
            cls=SimpleNamespace(tolist=list),
        )
        return [SimpleNamespace(names={}, boxes=boxes)]


def _detector() -> PlateDetector:
    # sin __init__: no carga ultralytics
    det = object.__new__(PlateDetector)
    det.model = FakeModel()
    det.conf, det.imgsz, det.min_rel, det.device = 0.25, 1280, 0.35, "cpu"
    return det


def test_detect_uses_image_size_for_pil_and_arrays():
    det = _detector()
    det.detect(Image.new("RGB", (350, 245)))
    det.detect(np.zeros((480, 640, 3), dtype=np.uint8))
    det.detect(Image.new("RGB", (1920, 1080)))
    assert [k["imgsz"] for k in det.model.kwargs] == [352, 640, 1280]
