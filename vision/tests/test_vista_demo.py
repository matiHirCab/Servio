"""Tests de la vista de demo (sin ventana)."""

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import cv2
import numpy as np
import pytest

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


def _video(tmp_path, seconds: int = 65) -> Path:
    video = tmp_path / "salon.avi"
    writer = cv2.VideoWriter(
        str(video), cv2.VideoWriter_fourcc(*"MJPG"), 10, (400, 200)
    )
    for _ in range(10 * seconds):
        writer.write(np.full((200, 400, 3), 90, np.uint8))
    writer.release()
    return video


def _run(tmp_path, monkeypatch, caplog, source, *extra) -> int:
    zones_path = tmp_path / "zones.json"
    save_zones(ZONES, zones_path)
    monkeypatch.delenv("DEMO_MODE", raising=False)
    caplog.set_level("INFO")
    args = ["--source", str(source), "--zones", str(zones_path), "--no-window"]
    return vista_demo.main([*args, *extra])


def test_records_video_with_last_reading(tmp_path, monkeypatch, caplog):
    out = tmp_path / "out" / "demo.mp4"
    code = _run(tmp_path, monkeypatch, caplog, _video(tmp_path), "--record", str(out))
    assert code == 0
    assert "3 lecturas" in caplog.text  # 0, 30 y 60 s: la última también cuenta
    recorded = cv2.VideoCapture(str(out))
    # 650 frames del video + 1 con la última lectura ya dibujada
    assert int(recorded.get(cv2.CAP_PROP_FRAME_COUNT)) == 651
    recorded.release()


def test_limit_stops_after_n_readings(tmp_path, monkeypatch, caplog):
    code = _run(tmp_path, monkeypatch, caplog, _video(tmp_path), "--limit", "1")
    assert code == 0
    assert "1 lecturas" in caplog.text


def test_folder_source(tmp_path, monkeypatch, caplog):
    photos = tmp_path / "fotos"
    photos.mkdir()
    for i in range(2):
        cv2.imwrite(str(photos / f"{i}.jpg"), np.zeros((200, 400, 3), np.uint8))
    assert _run(tmp_path, monkeypatch, caplog, photos) == 0
    assert "2 lecturas" in caplog.text


def test_missing_source_exits_with_message(tmp_path, monkeypatch, caplog):
    code = _run(tmp_path, monkeypatch, caplog, tmp_path / "no_existe.mp4")
    assert code == 2
    assert "No existe la fuente" in caplog.text


def test_overlay_boxes_match_crop_at_frame_edge():
    # la mesa se sale por la izquierda: el recorte va de x=0 a x=100
    zones = Zones(
        frame_size=(400, 200),
        tables=[
            TableZone(
                table_id="T1",
                name="Mesa 1",
                polygon=[(-100, 0), (100, 0), (100, 199), (-100, 199)],
            )
        ],
    )
    state = vista_demo.DemoState(zones)
    state.update(FakePipeline({"T1": [plate("vacio", 0.0)]}), T0)
    image = vista_demo.draw_overlay(
        np.zeros((200, 400, 3), np.uint8), zones, state, T0, "Servio"
    )
    # bbox x de 0.25 a 0.75 del recorte (ancho 101) → bordes en x=25 y x=76
    assert tuple(image[100, 25]) == vista_demo.PLATE_COLORS["vacio"]
    assert tuple(image[100, 76]) == vista_demo.PLATE_COLORS["vacio"]


def test_interval_zero_exits_with_message(tmp_path, monkeypatch, caplog):
    code = _run(tmp_path, monkeypatch, caplog, _video(tmp_path), "--interval", "0")
    assert code == 2
    assert "--interval tiene que ser mayor a 0" in caplog.text


def test_recorder_keeps_each_photo_for_the_interval(tmp_path):
    out = tmp_path / "fotos.mp4"
    recorder = vista_demo._Recorder(out, fps=1 / 1.5)  # carpeta con --interval 1.5
    recorder.write(np.zeros((20, 40, 3), np.uint8))
    recorder.close()
    recorded = cv2.VideoCapture(str(out))
    frames = recorded.get(cv2.CAP_PROP_FRAME_COUNT)
    fps = recorded.get(cv2.CAP_PROP_FPS)
    recorded.release()
    assert frames / fps == 1.5


def test_failing_pipeline_still_closes_the_recording(tmp_path, monkeypatch, caplog):
    def broken(self, frame, ts):
        raise RuntimeError("se rompió")

    monkeypatch.setattr(vista_demo.Pipeline, "process_frame", broken)
    out = tmp_path / "demo.mp4"
    with pytest.raises(RuntimeError):
        _run(tmp_path, monkeypatch, caplog, _video(tmp_path), "--record", str(out))
    recorded = cv2.VideoCapture(str(out))
    assert (
        recorded.get(cv2.CAP_PROP_FRAME_COUNT) > 0
    )  # el video quedó cerrado y legible
    recorded.release()
