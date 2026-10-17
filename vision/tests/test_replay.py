"""Tests de `tools/replay.py` (MAT-6): corridas, umbrales y tiempos."""

import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

from servio_vision import alerts
from tests.test_alert_engine import FIXTURES, PROD, obs
from tests.test_alert_process import DEMO, VACIO

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import replay as replay_tool
from replay import read_runs, replay

GAP = 300  # RUN_GAP_READINGS × 30 s


@pytest.fixture(autouse=True)
def reset_alerts(monkeypatch):
    # Cada test arranca sin el procesador global de alerts y cierra el log que abrió.
    monkeypatch.setattr(alerts, "_processor", None)
    yield
    if alerts._processor is not None:
        alerts._processor.close()


def observation_line(seconds: int) -> dict:
    return {"event": "observation", **obs(seconds, VACIO).model_dump(mode="json")}


def write_jsonl(path: Path, lines: list[dict]) -> Path:
    path.write_text("".join(json.dumps(line) + "\n" for line in lines), "utf-8")
    return path


def read_log(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text("utf-8").splitlines()]


def run_main(monkeypatch, *args: str) -> int:
    monkeypatch.setattr(sys, "argv", ["replay.py", "--no-telegram", *args])
    return replay_tool.main()


def test_mesa_completa_paced_and_matches_expected(tmp_path):
    log = tmp_path / "replay.jsonl"
    p = alerts.configure(PROD, telegram=False, log_path=log)
    (run,) = read_runs(FIXTURES / "observations" / "mesa_completa.jsonl", GAP)
    assert run.config is None  # fixture: sin run_started
    waits: list[float] = []
    count = replay(run.observations, p.process, speed=10, sleep=waits.append)

    assert sum(waits) == pytest.approx(78)  # 780 s de video a 10×
    assert all(w == pytest.approx(3) for w in waits)
    expected = json.loads(
        (FIXTURES / "expected" / "mesa_completa.alerts.json").read_text()
    )
    start = run.observations[0].ts
    got = [
        {
            "at_seconds": int(
                (datetime.fromisoformat(e["ts"]) - start).total_seconds()
            ),
            "event": e["event"],
            "type": e["type"],
            "table_id": e["table_id"],
        }
        for e in read_log(log)
        if e["event"].startswith("alert_")
    ]
    assert got == expected
    assert count == len(expected)


def test_runs_split_on_run_started_even_minutes_apart(tmp_path):
    log = tmp_path / "eventos.jsonl"
    first = [obs(s, VACIO) for s in (0, 30, 60, 90)]
    second = [obs(s, VACIO) for s in (120, 150)]  # 30 s después: mismo ritmo
    for config, observations in ((PROD, first), (DEMO, second)):
        p = alerts.configure(config, telegram=False, log_path=log)
        for o in observations:
            p.process(o)
    alerts._processor.close()

    runs = read_runs(log, GAP)
    assert [r.observations for r in runs] == [first, second]
    assert [r.config for r in runs] == [PROD, DEMO]


def test_legacy_log_splits_on_backwards_and_long_gaps(tmp_path):
    seconds = [0, 30, 60, 10, 40, 7200, 7230]
    path = write_jsonl(tmp_path / "viejo.jsonl", [observation_line(s) for s in seconds])
    runs = read_runs(path, GAP)
    assert [[o.ts.second + o.ts.minute * 60 for o in r.observations] for r in runs] == [
        [0, 30, 60],
        [10, 40],
        [0, 30],  # 7200 s = 2 h: mismo minuto y segundo
    ]
    assert all(r.config is None for r in runs)


def test_invalid_lines_are_skipped_with_line_number(tmp_path, caplog):
    path = tmp_path / "malo.jsonl"
    lines = [
        json.dumps(observation_line(0)),
        '{"event": "observation", "ts": 1}',
        "[]",
        "{roto",
        json.dumps(observation_line(30)),
    ]
    path.write_text("\n".join(lines) + "\n", "utf-8")
    (run,) = read_runs(path, GAP)
    assert len(run.observations) == 2
    for number in (2, 3, 4):
        assert f"malo.jsonl:{number}: línea inválida" in caplog.text


def test_main_replays_last_run_with_its_config(tmp_path, monkeypatch, capsys):
    source = tmp_path / "eventos.jsonl"
    for config in (PROD, DEMO):
        p = alerts.configure(config, telegram=False, log_path=source)
        for s in (0, 30, 60):
            p.process(obs(s, VACIO))
    alerts._processor.close()
    out = tmp_path / "replay.jsonl"
    out.write_text("de un replay anterior\n", "utf-8")

    assert (
        run_main(
            monkeypatch, "--file", str(source), "--log", str(out), "--speed", "1e6"
        )
        == 0
    )
    assert "umbrales de la corrida: t1=60s" in capsys.readouterr().out
    lines = read_log(out)  # se pisó: una sola corrida
    assert lines[0]["event"] == "run_started"
    assert lines[0]["config"]["t1"] == DEMO.t1
    assert sum(e["event"] == "observation" for e in lines) == 3


@pytest.mark.parametrize("same", ["file", "eventos"])
def test_main_refuses_to_overwrite_source_or_main_log(tmp_path, monkeypatch, same):
    source = write_jsonl(tmp_path / "eventos.jsonl", [observation_line(0)])
    log = source if same == "file" else alerts.DEFAULT_LOG
    assert run_main(monkeypatch, "--file", str(source), "--log", str(log)) == 1
    assert source.exists()
