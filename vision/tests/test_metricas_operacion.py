"""Métricas de operación de M-1: mesa terminada sin atender, mesas olvidadas,
rotación y desperdicio en $ (docs/context/13-metricas.md)."""

import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from servio_vision.db import Database
from servio_vision.metrics import FORGOTTEN_LIMIT_S, operation, print_report
from servio_vision.restaurant import Restaurant

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import metricas

T0 = datetime(2026, 10, 18, 21, 0, tzinfo=UTC)


def _db(tmp_path) -> Database:
    db = Database(tmp_path / "servio.db")
    db.load_restaurant(Restaurant.load())
    return db


def _answer(db, alert_id, text, seconds):
    db.feedback(
        {
            "alert_id": alert_id,
            "table_id": "T2",
            "answer": text,
            "free_text": False,
            "seconds_to_answer": seconds,
            "ts": "2026-10-18T21:10:00Z",
        }
    )


def test_one_visit_gives_every_operation_metric(tmp_path):
    db = _db(tmp_path)
    lomo = db.served("T2", "lomo", "T2-p1", T0)
    risotto = db.served("T2", "risotto", "T2-p2", T0 + timedelta(minutes=2))
    db.alert_opened("a1", "plato_lleno_mucho", "T2", lomo, T0 + timedelta(minutes=5))
    _answer(db, "a1", "Está todo bien", 30)
    db.alert_opened("a2", "plato_lleno_mucho", "T2", risotto, T0 + timedelta(minutes=6))
    _answer(db, "a2", "Estaba frío", FORGOTTEN_LIMIT_S + 60)
    db.alert_opened("a3", "mesa_terminada", "T2", None, T0 + timedelta(minutes=40))
    db.alert_resolved("a3", T0 + timedelta(minutes=48), "retirados")

    ops = operation(db)
    assert ops["unattended"]["mesas"] == 1
    assert round(ops["unattended"]["promedio"]) == 8
    assert ops["unattended"]["fuera_de_tiempo"] == 1
    assert ops["forgotten"] == [("Sofía", 2, 1)]
    assert ops["rotation"]["visits"] == 1
    assert round(ops["rotation"]["minutes"]) == 48
    assert ops["rotation"]["by_kind"].keys() == {"cena"}
    # el lomo volvió pero "Está todo bien": no es desperdicio
    assert ops["waste"] == [("Risotto de hongos", 1, 690.0)]
    db.close()


def test_a_new_dish_does_not_close_the_visit(tmp_path):
    db = _db(tmp_path)
    db.served("T1", "cookies", "T1-p1", T0)
    db.alert_opened("t1", "mesa_terminada", "T1", None, T0 + timedelta(minutes=10))
    db.alert_resolved("t1", T0 + timedelta(minutes=11), "servido")
    db.served("T1", "donas", "T1-p2", T0 + timedelta(minutes=11))
    assert operation(db)["rotation"] is None  # nadie retiró todavía
    db.alert_opened("t2", "mesa_terminada", "T1", None, T0 + timedelta(minutes=30))
    db.alert_resolved("t2", T0 + timedelta(minutes=32), "retirados")
    rot = operation(db)["rotation"]
    assert (rot["visits"], round(rot["minutes"])) == (1, 32)
    db.close()


def test_history_gives_numbers_and_roles_filter_money(tmp_path, capsys):
    db = _db(tmp_path)
    db.import_history(Path(metricas.HISTORY), Restaurant.load())
    m = metricas.compute(db)
    ops = m["ops"]
    assert ops["rotation"]["services"] == 60
    assert ops["forgotten"][0][0] == "Martín"  # el patrón simulado
    assert ops["waste"][0][0] == "Risotto de hongos"
    print_report(m, {"rotacion", "mesas_olvidadas"})
    out = capsys.readouterr().out
    assert "rotación" in out and "Desperdicio" not in out
    db.close()


def test_old_database_gets_the_price_column(tmp_path):
    path = tmp_path / "servio.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE platos (id TEXT PRIMARY KEY, nombre TEXT)")
    conn.close()
    db = _db(tmp_path)
    [row] = db.query("SELECT precio FROM platos WHERE id = 'lomo'")
    assert row["precio"] == 890
    db.close()
