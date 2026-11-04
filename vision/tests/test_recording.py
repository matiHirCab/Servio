"""Grabar y reproducir sesiones (B-1): --record deja una sesión completa y
--source DIR la reproduce con las mismas horas, la misma cadencia y los mismos avisos."""

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import cv2
import numpy as np
import pytest

import main as loop
from servio_vision import alerts, recording
from servio_vision.capture import open_source
from servio_vision.zones import TableZone, Zones, save_zones

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import comparar_sesion

ZONES = Zones(
    frame_size=(200, 100),
    tables=[
        TableZone(table_id="T1", name="Mesa 1", polygon=[(0, 0), (90, 0), (90, 90)]),
        TableZone(
            table_id="T2", name="Mesa 2", polygon=[(110, 0), (199, 0), (199, 90)]
        ),
    ],
)


def _video(tmp_path, seconds=400) -> Path:
    video = tmp_path / "salon.avi"
    writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"MJPG"), 5, (200, 100))
    frame = np.full((100, 200, 3), 90, np.uint8)
    for _ in range(5 * seconds):
        writer.write(frame)
    writer.release()
    return video


def _run(tmp_path, monkeypatch, log: Path, *args) -> int:
    monkeypatch.delenv("DEMO_MODE", raising=False)
    monkeypatch.setattr(alerts, "DEFAULT_LOG", log)
    monkeypatch.setattr(alerts, "_processor", None)
    code = loop.main(["--no-telegram", "--demo", *args])
    if alerts._processor is not None:
        alerts._processor.close()
    return code


def test_record_leaves_a_complete_session_and_replay_gives_the_same_alerts(
    tmp_path, monkeypatch
):
    """Hecho cuando: al reproducir salen los mismos avisos que en vivo. Acá con el
    clasificador falso (determinista), que es lo que el test puede garantizar."""
    zones = tmp_path / "zones.json"
    save_zones(ZONES, zones)
    session = tmp_path / "sesion"
    live_log = tmp_path / "vivo.jsonl"
    code = _run(
        tmp_path,
        monkeypatch,
        live_log,
        "--source",
        str(_video(tmp_path)),
        "--zones",
        str(zones),
        "--classifier",
        "fake",
        "--interval",
        "30",
        "--record",
        str(session),
    )
    assert code == 0
    # la sesión: frames con su hora, sesion.json y los eventos de la corrida
    frames = recording.session_frames(session)
    assert len(frames) == 14  # 400 s cada 30 s
    assert all(ts.tzinfo is UTC for _, ts in frames)
    info = recording.read_info(session)
    assert info["interval"] == 30 and info["readings"] == 14
    assert info["zones"]["tables"][0]["table_id"] == "T1" and "ended_at" in info
    recorded = (session / recording.EVENTS_FILE).read_text("utf-8").splitlines()
    assert recorded[0].startswith('{"event": "run_started"')
    assert recording.alert_signature(recorded)  # con --demo el fake dispara avisos

    # reproducir: sin --zones ni --interval, salen de la sesión
    replay_log = tmp_path / "replay.jsonl"
    code = _run(
        tmp_path,
        monkeypatch,
        replay_log,
        "--source",
        str(session),
        "--classifier",
        "fake",
    )
    assert code == 0
    assert comparar_sesion.compare(recorded, recording.last_run_lines(replay_log)) == []
    assert comparar_sesion.main([str(session), "--log", str(replay_log)]) == 0


def test_session_frames_keep_their_real_times(tmp_path):
    session = tmp_path / "s"
    session.mkdir()
    (session / recording.SESSION_FILE).write_text('{"interval": 10}', "utf-8")
    t0 = datetime(2026, 11, 4, 21, 0, tzinfo=UTC)
    times = [t0, t0.replace(second=12), t0.replace(second=41)]  # cadencia irregular
    for t in times:
        cv2.imwrite(
            str(session / recording.frame_name(t)), np.zeros((20, 20, 3), np.uint8)
        )
    (session / "notas.jpg").write_bytes(b"no es un frame")
    read = [ts for _, ts in open_source(str(session), interval=999)]
    assert read == times  # las horas grabadas, no cada 999 s
    assert recording.is_session(session) and not recording.is_session(tmp_path)


def test_compare_explains_differences():
    a = json.dumps(
        {"event": "alert_opened", "type": "mesa_terminada", "table_id": "T1", "ts": "t"}
    )
    b = json.dumps(
        {"event": "alert_opened", "type": "plato_vacio", "table_id": "T1", "ts": "t"}
    )
    assert comparar_sesion.compare([a], [a]) == []
    diffs = comparar_sesion.compare([a], [b])
    assert any("grabado y no reproducido" in d for d in diffs)
    assert any("reproducido y no grabado" in d for d in diffs)
    assert comparar_sesion.compare([a, b], [b, a]) == [
        "los mismos avisos, en otro orden"
    ]


def test_session_without_frames_or_info_explains(tmp_path):
    empty = tmp_path / "vacia"
    empty.mkdir()
    (empty / recording.SESSION_FILE).write_text("{}", "utf-8")
    with pytest.raises(FileNotFoundError, match="no tiene frames"):
        recording.session_frames(empty)
    (empty / recording.SESSION_FILE).write_text("{ roto", "utf-8")
    with pytest.raises(OSError, match="no se puede leer"):
        recording.read_info(empty)
