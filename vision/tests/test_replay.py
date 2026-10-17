"""Tests de `tools/replay.py` (MAT-6): lectura de fixtures y logs, y tiempos."""

import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

from servio_vision import alerts
from tests.test_alert_engine import FIXTURES, PROD, obs

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from replay import read_observations, replay

VACIO = ("T1-p1", "vacio", "centro")


@pytest.fixture(autouse=True)
def reset_alerts(monkeypatch):
    # Cada test arranca sin el procesador global de alerts y cierra el log que abrió.
    monkeypatch.setattr(alerts, "_processor", None)
    yield
    if alerts._processor is not None:
        alerts._processor.close()


def write_jsonl(path: Path, lines: list[dict]) -> Path:
    path.write_text("".join(json.dumps(line) + "\n" for line in lines), "utf-8")
    return path


def test_mesa_completa_paced_and_matches_expected(tmp_path):
    log = tmp_path / "replay.jsonl"
    alerts.configure(PROD, telegram=False, log_path=log)
    observations = read_observations(FIXTURES / "observations" / "mesa_completa.jsonl")
    waits: list[float] = []
    count = replay(observations, alerts.process, speed=10, sleep=waits.append)

    assert sum(waits) == pytest.approx(78)  # 780 s de video a 10×
    assert all(w == pytest.approx(3) for w in waits)
    expected = json.loads(
        (FIXTURES / "expected" / "mesa_completa.alerts.json").read_text()
    )
    start = observations[0].ts
    events = [json.loads(line) for line in log.read_text("utf-8").splitlines()]
    got = [
        {
            "at_seconds": int(
                (datetime.fromisoformat(e["ts"]) - start).total_seconds()
            ),
            "event": e["event"],
            "type": e["type"],
            "table_id": e["table_id"],
        }
        for e in events
        if e["event"] != "observation"
    ]
    assert got == expected
    assert count == len(expected)


def test_event_log_yields_only_observations(tmp_path):
    log = tmp_path / "eventos.jsonl"
    alerts.configure(PROD, telegram=False, log_path=log)
    originals = [obs(s, VACIO) for s in (0, 30, 60, 90)]
    for o in originals:
        alerts.process(o)
    alerts._processor.close()
    assert len(log.read_text("utf-8").splitlines()) > len(originals)  # hay alert_*
    assert read_observations(log) == originals


def test_log_with_two_runs_does_not_wait_backwards(tmp_path, caplog):
    run = [
        {"event": "observation", **o.model_dump(mode="json")}
        for o in (obs(0, VACIO), obs(30, VACIO))
    ]
    path = write_jsonl(tmp_path / "eventos.jsonl", run + run)
    alerts.configure(PROD, telegram=False, log_path=tmp_path / "replay.jsonl")
    waits: list[float] = []
    replay(read_observations(path), alerts.process, speed=10, sleep=waits.append)
    assert waits == [3, 3]  # sin espera negativa entre corridas
    assert "observación rechazada" in caplog.text


def test_invalid_line_reports_line_number(tmp_path):
    path = tmp_path / "malo.jsonl"
    path.write_text('\n{"event": "observation", "ts": 1}\n', "utf-8")
    with pytest.raises(ValueError, match="malo.jsonl:2"):
        read_observations(path)
