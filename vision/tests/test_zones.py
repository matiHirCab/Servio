"""Tests de zonas: recorte por mesa y editor de draw_zones (sin ventana)."""

import sys
from pathlib import Path

import cv2
import numpy as np
import pytest
from pydantic import ValidationError

from servio_vision.zones import (
    TableZone,
    Zones,
    crop_table,
    crop_tables,
    load_zones,
    save_zones,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from draw_zones import ZoneEditor

ZONES = Zones(
    frame_size=(200, 100),
    tables=[
        TableZone(table_id="T1", name="Mesa 1", polygon=[(10, 10), (60, 10), (60, 40)]),
        TableZone(
            table_id="T2",
            name="Mesa 2",
            polygon=[(100, 50), (190, 50), (190, 99), (100, 99)],
        ),
    ],
)


def test_crop_is_bounding_rect_of_polygon():
    frame = np.zeros((100, 200, 3), np.uint8)
    assert crop_table(frame, ZONES.tables[0], ZONES).shape == (31, 51, 3)
    assert crop_table(frame, ZONES.tables[1], ZONES).shape == (50, 91, 3)


def test_crop_scales_when_resolution_changes():
    frame = np.zeros((200, 400, 3), np.uint8)  # el doble que al marcar
    assert crop_table(frame, ZONES.tables[1], ZONES).shape == (99, 181, 3)


def test_crop_tables_returns_jpeg_per_table():
    frame = np.full((100, 200, 3), 128, np.uint8)
    crops = crop_tables(frame, ZONES)
    assert set(crops) == {"T1", "T2"}
    decoded = cv2.imdecode(np.frombuffer(crops["T2"], np.uint8), cv2.IMREAD_COLOR)
    assert decoded.shape == (50, 91, 3)


def test_save_and_load_round_trip(tmp_path):
    path = tmp_path / "config" / "zones.json"
    save_zones(ZONES, path)
    assert load_zones(path) == ZONES


def test_zones_reject_bad_input():
    with pytest.raises(ValidationError):
        TableZone(table_id="T1", name="Mesa 1", polygon=[(0, 0), (1, 1)])
    with pytest.raises(ValidationError):
        Zones(frame_size=(10, 10), tables=[ZONES.tables[0], ZONES.tables[0]])


def test_editor_marks_tables_in_order():
    editor = ZoneEditor((200, 100))
    for p in [(10, 10), (60, 10), (60, 40)]:
        editor.add_point(*p)
    editor.add_point(999, -5)  # fuera del frame: se pega al borde
    editor.undo_point()
    assert editor.close_table().name == "Mesa 1"
    for p in [(100, 50), (190, 50)]:
        editor.add_point(*p)
    assert editor.close_table() is None  # menos de 3 puntos
    editor.add_point(190, 99)
    assert editor.close_table().table_id == "T2"
    assert editor.to_zones().tables[0].polygon == [(10, 10), (60, 10), (60, 40)]


def test_editor_reuses_free_numbers_after_delete():
    editor = ZoneEditor.from_zones(ZONES, (200, 100))
    editor.delete_last_table()  # borra T2
    for p in [(0, 0), (5, 0), (5, 5)]:
        editor.add_point(*p)
    assert editor.close_table().table_id == "T2"


def test_editor_loads_zones_at_another_resolution():
    editor = ZoneEditor.from_zones(ZONES, (400, 200))
    assert editor.tables[0].polygon == [(20, 20), (120, 20), (120, 80)]
