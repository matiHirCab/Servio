import json
import sys
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import probar_usuario

from servio_vision.zones import load_zones

SALON = {
    "mozos": {"juan": {"nombre": "Juan"}, "sofia": {"nombre": "Sofía"}},
    "mesas": {"T1": {"mozo": "juan"}, "T2": {"mozo": "sofia"}},
}


def alert(table, message, event="alert_opened"):
    return {
        "event": event,
        "ts": "2026-10-29T00:20:04Z",
        "table_id": table,
        "message": message,
    }


def test_video_and_zones_match(tmp_path, monkeypatch):
    monkeypatch.setattr(probar_usuario, "TEST_DIR", tmp_path)
    video, zones_path = probar_usuario.make_video(tables=3, readings=2, interval=2)
    zones = load_zones(zones_path)
    assert [t.table_id for t in zones.tables] == ["T1", "T2", "T3"]
    cap = cv2.VideoCapture(str(video))
    assert int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) == zones.frame_size[0]
    assert int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) == 3 * 2  # lecturas + 1, 2 s cada una
    cap.release()


def test_each_waiter_gets_only_their_tables():
    events = [
        {"event": "observation"},
        alert("T1", "🍽️ Juan · Mesa 1: terminó."),
        alert("T2", "❓ Sofía · Mesa 2: Lomo sigue lleno hace 1 min."),
    ]
    by_waiter, wrong = probar_usuario.waiter_reports(events, SALON)
    assert list(by_waiter) == ["Juan", "Sofía"]
    assert "Mesa 1" in by_waiter["Juan"][0] and len(by_waiter["Juan"]) == 1
    assert wrong == []


def test_alert_naming_another_waiter_is_flagged():
    _, wrong = probar_usuario.waiter_reports(
        [alert("T2", "🍽️ Juan · Mesa 2: terminó.")], SALON
    )
    assert wrong == ["T2: 🍽️ Juan · Mesa 2: terminó."]


def test_last_run_starts_at_the_last_run_started(tmp_path):
    log = tmp_path / "eventos.jsonl"
    rows = [
        {"event": "run_started"},
        {"event": "a"},
        {"event": "run_started"},
        {"event": "b"},
    ]
    log.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    assert [e["event"] for e in probar_usuario.last_run(log)] == ["run_started", "b"]
