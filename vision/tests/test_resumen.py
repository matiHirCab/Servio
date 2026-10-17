"""Tests de la tabla de lo que pasó (tools/resumen.py)."""

import json
import pathlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import resumen


def obs(minute: float, table: str, plates: int) -> dict:
    """Observación válida según el contrato (`TableObservation`)."""
    items = [
        {
            "track_id": f"{table}-p{i}",
            "state": "lleno",
            "fill_ratio": 0.9,
            "confidence": 0.9,
            "position": f"pos{i}",
        }
        for i in range(plates)
    ]
    return {
        "event": "observation",
        "schema_version": 1,
        "camera_id": "cam-1",
        "ts": _ts(minute),
        "table_id": table,
        "people_present": True,
        "items": items,
    }


def alert(kind: str, minute: float, table: str, alert_id: str, type_: str) -> dict:
    return {
        "event": kind,
        "ts": _ts(minute),
        "table_id": table,
        "id": alert_id,
        "type": type_,
    }


def _ts(minute: float) -> str:
    total = round(minute * 60)
    return f"2026-10-17T20:{total // 60:02d}:{total % 60:02d}Z"


EVENTS = [
    obs(0, "T1", 0),
    obs(1, "T1", 2),  # empieza la comida
    alert("alert_opened", 3, "T1", "a1", "plato_lleno_mucho"),
    alert("alert_reminder", 4, "T1", "a1", "plato_lleno_mucho"),
    alert("alert_resolved", 5, "T1", "a1", "plato_lleno_mucho"),
    alert("alert_opened", 9, "T1", "a2", "plato_vacio"),
    obs(11, "T1", 0),  # retiran: comida de 10 min
    alert("alert_resolved", 11, "T1", "a2", "plato_vacio"),
    obs(2, "T2", 1),  # T2 sigue comiendo al final del log
    alert("alert_opened", 6, "T2", "a3", "mesa_por_terminar"),
]


def test_summary_per_table():
    tables = resumen.summarize(EVENTS)
    t1, t2 = tables["T1"], tables["T2"]
    assert t1.meals == [600.0] and t1.meals_in_progress == 0
    assert t1.alerts == {"plato_lleno_mucho": 1, "plato_vacio": 1}
    assert t1.reminders == 1
    assert t1.resolve_times == [120.0, 120.0]
    assert t2.meals == [] and t2.meals_in_progress == 1
    assert t2.alerts == {"mesa_por_terminar": 1}
    assert t2.resolve_times == []  # sigue abierto


def test_rows_have_totals():
    table = resumen.rows(resumen.summarize(EVENTS))
    assert table[0] == resumen.HEADERS
    assert table[1] == ["T1", "1", "10.0 min", "0", "1", "1", "1", "2.0 min"]
    assert table[2] == ["T2", "0 (+1 en curso)", "-", "1", "0", "0", "0", "-"]
    assert table[-1] == [
        "Total",
        "1 (+1 en curso)",
        "10.0 min",
        "1",
        "1",
        "1",
        "1",
        "2.0 min",
    ]


def test_cli_prints_table_and_saves_image(tmp_path, capsys):
    log = tmp_path / "eventos.jsonl"
    log.write_text("\n".join(json.dumps(e) for e in EVENTS), encoding="utf-8")
    image = tmp_path / "resumen.png"
    assert resumen.main(["--file", str(log), "--image", str(image)]) == 0
    out = capsys.readouterr().out
    assert "Duración media" in out and "Total" in out
    assert image.stat().st_size > 0


def test_cli_without_log(tmp_path, capsys):
    assert resumen.main(["--file", str(tmp_path / "no.jsonl")]) == 2
    assert "corré primero" in capsys.readouterr().out


def test_strip_accents():
    assert resumen._strip_accents("Duración vacíos") == "Duracion vacios"


def _write(tmp_path, events) -> pathlib.Path:
    log = tmp_path / "eventos.jsonl"
    log.write_text("\n".join(json.dumps(e) for e in events), encoding="utf-8")
    return log


def test_only_last_run_by_default(tmp_path, capsys):
    # ensayo: 30 min de video (lecturas cada 5 min, sin huecos que corten la corrida)
    rehearsal = [*(obs(m, "T1", 2) for m in range(0, 30, 5)), obs(30, "T1", 0)]
    demo = [obs(1, "T1", 2), obs(6, "T1", 0)]  # la hora vuelve atrás: otra corrida
    log = _write(tmp_path, rehearsal + demo)
    assert resumen.load_runs(log) == [rehearsal, demo]
    resumen.main(["--file", str(log)])
    assert "5.0 min" in capsys.readouterr().out
    resumen.main(["--file", str(log), "--all"])
    out = capsys.readouterr().out
    assert "| 2 " in out and "17.5 min" in out  # (30 + 5) / 2, sin mezclar corridas


def test_last_run_starts_at_run_started(tmp_path, capsys):
    started = {"event": "run_started", "started_at": _ts(0), "config": {}}
    first = [obs(1, "T1", 2), obs(4, "T1", 0)]
    second = [obs(5, "T1", 2), obs(12, "T1", 0)]  # la hora no vuelve atrás
    log = _write(tmp_path, [started, *first, started, *second])
    assert resumen.load_runs(log) == [first, second]
    resumen.main(["--file", str(log)])
    out = capsys.readouterr().out
    assert "7.0 min" in out and "salteo" not in out
    resumen.main(["--file", str(log), "--all"])
    assert "5.0 min" in capsys.readouterr().out  # (3 + 7) / 2


def test_alerts_stay_with_their_run(tmp_path):
    started = {"event": "run_started", "started_at": _ts(0), "config": {}}
    first = [obs(1, "T1", 2), alert("alert_opened", 2, "T1", "a1", "plato_vacio")]
    second = [obs(1, "T1", 2)]  # misma hora de video que la primera corrida
    log = _write(tmp_path, [started, *first, started, *second])
    last = resumen.summarize_runs(resumen.load_runs(log)[-1:])
    assert last["T1"].alerts == {}


def test_fixture_lines_without_event_are_observations(tmp_path):
    lines = [obs(1, "T1", 2), obs(5, "T1", 0)]
    for line in lines:
        del line["event"]
    [run] = resumen.load_runs(_write(tmp_path, lines))
    assert resumen.summarize(run)["T1"].meals == [240.0]


def test_backwards_observation_is_ignored():
    events = [obs(0, "T1", 2), obs(4, "T1", 2), obs(2, "T1", 0), obs(10, "T1", 0)]
    tables = resumen.summarize(events)
    assert tables["T1"].meals == [600.0]  # la de los 2 min no corta la comida


def test_broken_line_is_skipped(tmp_path, capsys):
    log = tmp_path / "eventos.jsonl"
    good = json.dumps(obs(1, "T1", 2))
    log.write_text(good + "\n" + '{"event": "observ', encoding="utf-8")
    assert len(resumen.load_runs(log)[0]) == 1
    assert "eventos.jsonl:2: línea inválida" in capsys.readouterr().out


def test_unknown_alert_type_gets_its_own_column():
    events = [*EVENTS, alert("alert_opened", 7, "T2", "a9", "cuenta")]
    table = resumen.rows(resumen.summarize(events))
    assert "Otros" in table[0]
    assert table[-1][table[0].index("Otros")] == "1"
