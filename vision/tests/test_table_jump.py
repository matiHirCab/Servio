"""Regresión: si el recuadro de la mesa cambia, las cajas de los platos no se corren.

Las `bbox` de los platos son relativas al recorte de la mesa. Cuando el detector de
mesas agranda o mueve el recuadro, todo lo que guarda cajas (lectura anterior,
suavizado, memoria de platos perdidos de `IdCounter`) tiene que pasar al recorte
nuevo. Si algo queda en el recorte viejo, el plato se dibuja corrido, Claude mira
otro lugar y el plato real se descarta (pasó en la demo con el plato de donas).
"""

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

import main as loop
from servio_vision.classifiers import claude_plates
from servio_vision.classifiers.claude_plates import PlateEstimate, PlatesReport
from servio_vision.classifiers.hybrid import HybridClassifier
from servio_vision.detector import Detection
from servio_vision.zones import TableZone, Zones

FRAME = np.zeros((400, 600, 3), np.uint8)
# platos quietos, en píxeles del frame: (x0, y0, x1, y1)
PLATES = {"donas": (150, 150, 230, 230), "cookies": (300, 150, 380, 230)}
SMALL = (100, 100, 400, 300)  # la mesa
BIG = (0, 50, 420, 320)  # la mesa con las piernas de alguien


class Scene:
    """Qué recuadro de mesa da el detector y qué platos ve YOLO en cada lectura."""

    def __init__(self) -> None:
        self.rect = SMALL
        self.visible = set(PLATES)

    # TableTracker falso
    def update(self, frame):
        x0, y0, x1, y1 = self.rect
        return Zones(
            frame_size=(600, 400),
            tables=[
                TableZone(
                    table_id="T1",
                    name="Mesa 1",
                    polygon=[(x0, y0), (x1, y0), (x1, y1), (x0, y1)],
                )
            ],
        )

    # PlateDetector falso: cajas en píxeles del recorte
    def detect(self, image):
        x0, y0 = self.rect[:2]
        return [
            Detection((a - x0, b - y0, c - x0, d - y0), 0.9, "plate")
            for name, (a, b, c, d) in PLATES.items()
            if name in self.visible
        ]


@pytest.fixture
def claude(monkeypatch):
    """Claude falso: todos los platos llenos y a la vista."""

    def estimate(table, plates, table_id, **kwargs):
        return PlatesReport(
            plates=[
                PlateEstimate(
                    index=i + 1, fill_ratio=0.95, confidence=0.9, visible=True
                )
                for i in range(len(plates))
            ]
        )

    monkeypatch.setattr(claude_plates, "estimate_plates", estimate)


def in_frame(bbox, rect):
    x0, y0, x1, y1 = rect
    w, h = x1 - x0, y1 - y0
    bx, by, bw, bh = bbox
    return (x0 + bx * w, y0 + by * h, x0 + (bx + bw) * w, y0 + (by + bh) * h)


def test_plate_boxes_stay_on_the_plates_when_the_table_box_jumps(claude):
    scene = Scene()
    pipeline = loop.Pipeline(
        HybridClassifier(detector=scene), None, lambda o: None, tables=scene
    )
    steps = [
        (SMALL, {"donas", "cookies"}),
        (BIG, {"donas", "cookies"}),  # la mesa se agranda
        (BIG, {"cookies"}),  # YOLO pierde las donas: van como plato perdido
        (SMALL, {"cookies"}),  # la mesa vuelve con las donas todavía perdidas
        (SMALL, {"donas", "cookies"}),
    ]
    t0 = datetime(2026, 10, 18, 20, 0, tzinfo=UTC)
    try:
        for n, (rect, visible) in enumerate(steps):
            scene.rect, scene.visible = rect, visible
            pipeline.process_frame(FRAME, t0 + timedelta(seconds=5 * n))
            items = pipeline.previous["T1"]
            assert sorted(i.id for i in items) == ["p1", "p2"], f"lectura {n}"
            boxes = sorted(in_frame(i.bbox, rect) for i in items)
            for got, want in zip(boxes, sorted(PLATES.values())):
                assert got == pytest.approx(want, abs=3), f"lectura {n}"
    finally:
        pipeline.close()
