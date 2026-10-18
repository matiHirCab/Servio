"""Tests del seguimiento de mesas (detector falso: no necesita YOLO)."""

import numpy as np

from servio_vision.tables import TableTracker, iou

FRAME = np.zeros((400, 600, 3), np.uint8)


class FakeDetector:
    def __init__(self, *readings):
        self.readings = list(readings)

    def detect(self, frame):
        return self.readings.pop(0)


def box_of(zones, table_id="T1"):
    [zone] = [t for t in zones.tables if t.table_id == table_id]
    xs = [x for x, _ in zone.polygon]
    ys = [y for _, y in zone.polygon]
    return min(xs), min(ys), max(xs), max(ys)


def test_no_table_ever_uses_the_whole_frame():
    zones = TableTracker(FakeDetector([])).update(FRAME)
    assert [(t.table_id, t.name) for t in zones.tables] == [("T1", "Mesa 1")]
    assert box_of(zones) == (0, 0, 599, 399)


def test_table_gets_a_name_and_a_margin():
    zones = TableTracker(FakeDetector([((100, 100, 300, 300), 0.5)])).update(FRAME)
    assert [t.name for t in zones.tables] == ["Mesa 1"]
    assert box_of(zones) == (80, 80, 320, 320)  # 10 % de margen por lado


def test_moving_table_keeps_its_id_and_follows():
    tracker = TableTracker(
        FakeDetector(
            [((100, 100, 300, 300), 0.5)],
            [((140, 100, 340, 300), 0.5)],  # se corrió 40 px
            [((140, 100, 340, 300), 0.5)],
        )
    )
    first = box_of(tracker.update(FRAME))
    second = box_of(tracker.update(FRAME))
    third = box_of(tracker.update(FRAME))
    assert first[0] < second[0] < third[0] < 140  # lo sigue, suavizado
    assert [t.table_id for t in tracker.tables] == ["T1"]


def test_missed_detection_keeps_the_last_position():
    tracker = TableTracker(FakeDetector([((100, 100, 300, 300), 0.5)], []))
    first = box_of(tracker.update(FRAME))
    assert box_of(tracker.update(FRAME)) == first


def test_second_table_gets_mesa_2_and_overlaps_are_ignored():
    tracker = TableTracker(
        FakeDetector(
            [((10, 10, 200, 200), 0.6)],
            [
                ((15, 15, 205, 205), 0.6),
                ((350, 50, 550, 300), 0.5),
                ((20, 20, 190, 190), 0.3),
            ],
        )
    )
    tracker.update(FRAME)
    zones = tracker.update(FRAME)
    assert [(t.table_id, t.name) for t in zones.tables] == [
        ("T1", "Mesa 1"),
        ("T2", "Mesa 2"),
    ]


def test_iou():
    assert iou((0, 0, 10, 10), (0, 0, 10, 10)) == 1
    assert iou((0, 0, 10, 10), (20, 20, 30, 30)) == 0


def test_table_dragged_far_is_still_mesa_1():
    tracker = TableTracker(
        FakeDetector([((100, 100, 300, 300), 0.5)], [((250, 150, 450, 350), 0.5)])
    )
    tracker.update(FRAME)
    zones = tracker.update(FRAME)  # la arrastraron 150 px: no hay superposición útil
    assert [t.name for t in zones.tables] == ["Mesa 1"]
    assert box_of(zones) == (230, 130, 470, 370)  # saltó a la posición nueva


def test_remap_bbox_keeps_the_plate_in_place():
    from servio_vision.zones import remap_bbox

    # plato en (150..200, 150..200) del frame; recorte viejo (100..300), nuevo (50..250)
    old, new = (100, 100, 300, 300), (50, 50, 250, 250)
    assert remap_bbox((0.25, 0.25, 0.25, 0.25), old, new) == (0.5, 0.5, 0.25, 0.25)
