"""Tests del seguimiento de mesas (detector falso: no necesita YOLO)."""

import numpy as np
import pytest

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


def test_new_tables_are_numbered_by_rows():
    # 2x2: la más segura es la de abajo a la derecha, pero es la Mesa 4
    detections = [
        ((300, 220, 450, 370), 0.9),  # abajo derecha
        ((20, 20, 170, 170), 0.3),  # arriba izquierda
        ((310, 30, 460, 180), 0.5),  # arriba derecha (un poco más abajo)
        ((10, 230, 160, 380), 0.6),  # abajo izquierda
    ]
    zones = TableTracker(FakeDetector(detections)).update(FRAME)
    named = {t.name: box_of(zones, t.table_id) for t in zones.tables}
    assert named["Mesa 1"][0] < 100 and named["Mesa 1"][1] < 100
    assert named["Mesa 2"][0] > 250 and named["Mesa 2"][1] < 100
    assert named["Mesa 3"][0] < 100 and named["Mesa 3"][1] > 150
    assert named["Mesa 4"][0] > 250 and named["Mesa 4"][1] > 150


def test_table_with_someone_glued_does_not_grow():
    # el detector a veces mete las piernas de alguien en la mesa: la caja grande
    # que contiene a la mesa se ignora y la mesa no se cuenta dos veces
    tracker = TableTracker(
        FakeDetector(
            [((100, 100, 300, 300), 0.5)],
            [((10, 20, 300, 300), 0.6)],
        )
    )
    first = box_of(tracker.update(FRAME))
    zones = tracker.update(FRAME)
    assert box_of(zones) == first and len(zones.tables) == 1


def test_lost_plate_memory_follows_the_table():
    from servio_vision.tracking import IdCounter
    from servio_vision.zones import remap_bbox

    ids = IdCounter()
    ids.assign("T1", [(0.5, 0.5, 0.2, 0.2)], [])
    ids.assign("T1", [], [])  # YOLO no lo ve: queda recordado
    old, new = (100, 100, 300, 300), (0, 0, 400, 400)
    ids.remap("T1", lambda b: remap_bbox(b, old, new))
    [(pid, bbox)] = ids.missing("T1")
    assert pid == "p1"
    assert bbox == pytest.approx((0.5, 0.5, 0.1, 0.1))  # mismo lugar del frame
