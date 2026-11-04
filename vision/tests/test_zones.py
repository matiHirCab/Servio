"""Tests de zonas: recorte por mesa y editor de draw_zones (sin ventana)."""

import sys
from datetime import datetime
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
    remap_bbox,
    save_zones,
    table_rect,
    to_jpeg,
)
from servio_vision.zones import main as zones_main

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
    editor = ZoneEditor((200, 100))
    for _ in range(2):
        for p in [(0, 0), (5, 0), (5, 5)]:
            editor.add_point(*p)
        editor.close_table()
    editor.delete_last_table()  # borra T2
    for p in [(0, 0), (5, 0), (5, 5)]:
        editor.add_point(*p)
    assert editor.close_table().table_id == "T2"


def test_remap_bbox_identity_when_the_crop_does_not_change():
    rect = (10, 10, 110, 60)
    assert remap_bbox((0.2, 0.2, 0.5, 0.5), rect, rect) == pytest.approx(
        (0.2, 0.2, 0.5, 0.5)
    )


def test_remap_bbox_into_a_bigger_crop_shrinks_the_box():
    old, new = (50, 50, 150, 150), (0, 0, 200, 200)
    assert remap_bbox((0.0, 0.0, 1.0, 1.0), old, new) == pytest.approx(
        (0.25, 0.25, 0.5, 0.5)
    )


def test_remap_bbox_is_clipped_to_the_new_crop():
    old, new = (0, 0, 100, 100), (50, 0, 150, 100)
    x, y, w, h = remap_bbox((0.0, 0.0, 1.0, 1.0), old, new)
    assert (x, w) == pytest.approx((0.0, 0.5))
    assert (y, h) == pytest.approx((0.0, 1.0))


def test_remap_bbox_outside_the_new_crop_is_none():
    assert (
        remap_bbox((0.0, 0.0, 0.2, 0.2), (0, 0, 100, 100), (80, 80, 180, 180)) is None
    )


def test_table_outside_the_frame_is_rejected():
    table = TableZone(
        table_id="T1", name="Mesa", polygon=[(-30, -30), (-20, -30), (-20, -20)]
    )
    zones = Zones(frame_size=(100, 100), tables=[table])
    frame = np.zeros((100, 100, 3), np.uint8)
    with pytest.raises(ValueError, match="fuera del frame"):
        table_rect(frame, table, zones)


def test_to_jpeg_returns_a_jpeg():
    assert to_jpeg(np.zeros((4, 4, 3), np.uint8))[:2] == b"\xff\xd8"


def test_main_saves_a_crop_per_table(tmp_path, monkeypatch, capsys):
    zones_path = tmp_path / "zones.json"
    save_zones(ZONES, zones_path)
    out = tmp_path / "crops"
    frame = np.full((100, 200, 3), 128, np.uint8)
    ts = datetime(2026, 10, 28, 20, 30, 15)
    monkeypatch.setattr(
        "servio_vision.capture.open_source", lambda *a, **k: iter([(frame, ts)])
    )

    code = zones_main(
        ["--source", "x", "--zones", str(zones_path), "--out", str(out), "--limit", "1"]
    )

    assert code == 0
    assert sorted(p.name for p in out.glob("*.jpg")) == [
        "203015_T1.jpg",
        "203015_T2.jpg",
    ]


def test_main_without_frames_exits_with_1(tmp_path, monkeypatch):
    zones_path = tmp_path / "zones.json"
    save_zones(ZONES, zones_path)
    monkeypatch.setattr("servio_vision.capture.open_source", lambda *a, **k: iter([]))
    assert (
        zones_main(
            ["--source", "x", "--zones", str(zones_path), "--out", str(tmp_path)]
        )
        == 1
    )


def test_main_reports_unreadable_source_with_exit_2(tmp_path, monkeypatch, capsys):
    zones_path = tmp_path / "zones.json"
    save_zones(ZONES, zones_path)

    def broken(*a, **k):
        raise OSError("no se puede abrir")

    monkeypatch.setattr("servio_vision.capture.open_source", broken)
    code = zones_main(
        ["--source", "x", "--zones", str(zones_path), "--out", str(tmp_path)]
    )
    assert code == 2
    assert "no se puede abrir" in capsys.readouterr().err
