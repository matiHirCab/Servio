"""Tests de la vista de demo (sin ventana)."""

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import cv2
import numpy as np

from servio_vision.schemas import Item
from servio_vision.zones import TableZone, Zones, save_zones

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import vista_demo

ZONES = Zones(
    frame_size=(400, 200),
    tables=[
        TableZone(
            table_id="T1",
            name="Mesa 1",
            polygon=[(10, 10), (190, 10), (190, 190), (10, 190)],
        ),
        TableZone(
            table_id="T2",
            name="Mesa 2",
            polygon=[(210, 10), (390, 10), (390, 190), (210, 190)],
        ),
    ],
)
T0 = datetime(2026, 10, 17, 20, 0, tzinfo=UTC)


class FakePipeline:
    def __init__(self, previous):
        self.previous = previous


def plate(state: str, fill: float) -> Item:
    return Item(
        id="p1",
        state=state,
        fill_ratio=fill,
        position="centro",
        confidence=0.9,
        bbox=(0.25, 0.25, 0.5, 0.5),
    )


def test_state_tracks_status_and_since():
    state = vista_demo.DemoState(ZONES)
    state.update(FakePipeline({"T1": [plate("lleno", 0.9)], "T2": []}), T0)
    later = T0 + timedelta(minutes=3)
    state.update(FakePipeline({"T1": [plate("lleno", 0.9)], "T2": []}), later)
    assert state.tables["T1"].status == "servida"
    assert state.tables["T1"].since == T0  # no cambió de estado
    state.update(FakePipeline({"T1": [plate("vacio", 0.0)], "T2": []}), later)
    assert state.tables["T1"].status == "terminada"
    assert state.tables["T1"].since == later
    assert state.tables["T2"].status == "libre"
    assert state.readings == 3


def test_overlay_draws_table_and_plate_colors():
    state = vista_demo.DemoState(ZONES)
    state.update(FakePipeline({"T1": [plate("vacio", 0.0)], "T2": []}), T0)
    frame = np.zeros((200, 400, 3), np.uint8)
    image = vista_demo.draw_overlay(frame, ZONES, state, T0, "Servio")
    red = vista_demo.TABLE_COLORS["terminada"]
    assert tuple(image[100, 10]) == red  # borde izquierdo de la Mesa 1
    assert tuple(image[100, 55]) == vista_demo.PLATE_COLORS["vacio"]  # caja del plato
    assert tuple(image[100, 210]) == vista_demo.TABLE_COLORS["libre"]
    assert frame.sum() == 0  # no toca el frame original


def test_records_video_without_window(tmp_path, monkeypatch):
    video = tmp_path / "salon.avi"
    writer = cv2.VideoWriter(
        str(video), cv2.VideoWriter_fourcc(*"MJPG"), 10, (400, 200)
    )
    for _ in range(10 * 65):
        writer.write(np.full((200, 400, 3), 90, np.uint8))
    writer.release()
    zones_path = tmp_path / "zones.json"
    save_zones(ZONES, zones_path)
    out = tmp_path / "out" / "demo.mp4"
    monkeypatch.delenv("DEMO_MODE", raising=False)

    code = vista_demo.main(
        [
            "--source",
            str(video),
            "--zones",
            str(zones_path),
            "--no-window",
            "--record",
            str(out),
        ]
    )

    assert code == 0
    recorded = cv2.VideoCapture(str(out))
    assert int(recorded.get(cv2.CAP_PROP_FRAME_COUNT)) == 650
    recorded.release()
