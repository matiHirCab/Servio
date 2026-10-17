"""Tests de ids estables y filtro de cajas (TOM-4)."""

from servio_vision.detector import Detection, filter_detections
from servio_vision.schemas import Item
from servio_vision.tracking import match_ids, position_label


def _item(pid, bbox):
    return Item(id=pid, state="lleno", position="centro", confidence=0.9, bbox=bbox)


def test_plate_that_moves_a_little_keeps_its_id():
    prev = [_item("p1", (0.1, 0.1, 0.2, 0.2)), _item("p2", (0.6, 0.6, 0.2, 0.2))]
    ids = match_ids([(0.62, 0.63, 0.2, 0.2), (0.12, 0.11, 0.2, 0.2)], prev)
    assert ids == ["p2", "p1"]


def test_new_plate_gets_next_id():
    prev = [_item("p1", (0.1, 0.1, 0.2, 0.2)), _item("p3", (0.6, 0.6, 0.2, 0.2))]
    ids = match_ids([(0.1, 0.1, 0.2, 0.2), (0.4, 0.05, 0.2, 0.2)], prev)
    assert ids == ["p1", "p4"]


def test_first_reading_numbers_from_one():
    assert match_ids([(0.1, 0.1, 0.2, 0.2), (0.5, 0.5, 0.2, 0.2)], []) == ["p1", "p2"]


def test_one_previous_plate_is_not_reused():
    prev = [_item("p1", (0.1, 0.1, 0.2, 0.2))]
    ids = match_ids([(0.1, 0.1, 0.2, 0.2), (0.12, 0.1, 0.2, 0.2)], prev)
    assert sorted(ids) == ["p1", "p2"]


def test_position_label():
    assert position_label((0.0, 0.0, 0.2, 0.2)) == "arriba-izquierda"
    assert position_label((0.4, 0.4, 0.2, 0.2)) == "centro"
    assert position_label((0.8, 0.4, 0.2, 0.2)) == "centro-derecha"


def test_filter_drops_duplicates_and_small_boxes():
    dets = [
        Detection((0, 0, 100, 100), 0.9, "plate"),
        Detection((2, 2, 98, 98), 0.6, "bowl"),  # mismo plato
        Detection((200, 0, 300, 100), 0.8, "plate"),
        Detection((400, 0, 420, 20), 0.7, "bowl"),  # aceituna
    ]
    kept = filter_detections(dets)
    assert [d.box for d in kept] == [(0, 0, 100, 100), (200, 0, 300, 100)]
