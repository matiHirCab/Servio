"""El pipeline clasifica varias mesas a la vez en hilos y comparten un solo YOLO."""

import threading
import time
from types import SimpleNamespace

from servio_vision.detector import PlateDetector


class _Tensor:
    def __init__(self, values):
        self.values = values

    def tolist(self):
        return self.values


class _OverlapDetectingModel:
    """Un modelo que anota si dos `predict` corrieron a la vez."""

    def __init__(self):
        self._guard = threading.Lock()
        self.active = 0
        self.max_active = 0

    def predict(self, image, **kwargs):
        with self._guard:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        time.sleep(0.02)  # da tiempo a que otro hilo entre si no hay candado
        with self._guard:
            self.active -= 1
        boxes = SimpleNamespace(
            xyxy=_Tensor([[0, 0, 10, 10]]), conf=_Tensor([0.9]), cls=_Tensor([0])
        )
        return [SimpleNamespace(names={0: "plate"}, boxes=boxes)]


def test_plate_detector_never_runs_the_model_from_two_threads_at_once():
    model = _OverlapDetectingModel()
    detector = PlateDetector.__new__(PlateDetector)  # sin cargar pesos de verdad
    detector.model = model
    detector.conf, detector.imgsz, detector.min_rel, detector.device = (
        0.25,
        640,
        0.35,
        "cpu",
    )
    detector._predict_lock = threading.Lock()

    results: list[int] = []
    threads = [
        threading.Thread(target=lambda: results.append(len(detector.detect(None))))
        for _ in range(6)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results == [1] * 6
    assert model.max_active == 1
