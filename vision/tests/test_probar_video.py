"""probar_video: en paralelo da lo mismo que de a un frame por vez."""

import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import probar_video

from servio_vision.classifiers import claude_plates
from servio_vision.classifiers.claude_plates import (
    PlateEstimate,
    PlatesReport,
)
from servio_vision.classifiers.hybrid import HybridClassifier
from servio_vision.detector import Detection

BOXES = [
    [
        Detection((20, 20, 100, 100), 0.9, "plate"),
        Detection((220, 20, 300, 100), 0.9, "plate"),
    ],
    [Detection((220, 20, 300, 100), 0.9, "plate")],  # YOLO pierde un plato
    [
        Detection((20, 20, 100, 100), 0.9, "plate"),
        Detection((220, 20, 300, 100), 0.9, "plate"),
    ],
]


class ScriptedDetector:
    def __init__(self):
        self.n = 0

    def detect(self, img):
        self.n += 1
        return BOXES[self.n - 1]


def _frames():
    for n in range(len(BOXES)):
        yield float(n * 20), Image.new("RGB", (400, 200), "white")


def _run(monkeypatch, tmp_path, parallel: bool):
    def estimate(table, plates, table_id, **kwargs):
        return PlatesReport(
            plates=[
                PlateEstimate(index=i, fill_ratio=0.5, confidence=0.8)
                for i in range(1, len(plates) + 1)
            ]
        )

    monkeypatch.setattr(claude_plates, "estimate_plates", estimate)
    tmp_path.mkdir()
    clf = HybridClassifier(detector=ScriptedDetector())
    timeline: list = []
    if parallel:
        probar_video.run_parallel(clf, _frames(), tmp_path, timeline, workers=2)
    else:
        probar_video.run_serial(clf, _frames(), tmp_path, timeline)
    return [
        sorted((i["id"], i["state"]) for i in f["report"]["items"]) for f in timeline
    ]


def test_parallel_matches_serial(monkeypatch, tmp_path):
    serial = _run(monkeypatch, tmp_path / "a", parallel=False)
    parallel = _run(monkeypatch, tmp_path / "b", parallel=True)
    assert serial == parallel
    # el plato que YOLO perdió en el segundo frame sigue, porque Claude lo ve
    assert [len(f) for f in serial] == [2, 2, 2]
