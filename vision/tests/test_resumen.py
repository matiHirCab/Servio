"""Tests de la tabla de lo que pasó (tools/resumen.py)."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import resumen


def obs(minute: float, table: str, plates: int) -> dict:
    items = [{"track_id": f"{table}-p{i}"} for i in range(plates)]
    return {
        "event": "observation",
        "ts": _ts(minute),
        "table_id": table,
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
    assert t1.meals == [600.0] and not t1.meal_in_progress
    assert t1.alerts == {"plato_lleno_mucho": 1, "plato_vacio": 1}
    assert t1.reminders == 1
    assert t1.resolve_times == [120.0, 120.0]
    assert t2.meals == [] and t2.meal_in_progress
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
