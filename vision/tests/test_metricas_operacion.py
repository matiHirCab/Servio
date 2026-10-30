"""Métricas de operación de M-1: mesa terminada sin atender, mesas olvidadas,
rotación y desperdicio en $ (docs/context/13-metricas.md)."""

import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from servio_vision.db import Database
from servio_vision.metrics import (
    FORGOTTEN_LIMIT_S,
    VISIT_GAP_MIN,
    operation_metrics,
    print_report,
    table_visits,
)
from servio_vision.restaurant import Restaurant

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import metricas

# 21:00 del 18/10 en Montevideo (UTC−3): la cámara guarda en UTC.
T0 = datetime(2026, 10, 19, 0, 0, tzinfo=UTC)


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

    ops = operation_metrics(db)
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
    assert operation_metrics(db)["rotation"] is None  # nadie retiró todavía
    db.alert_opened("t2", "mesa_terminada", "T1", None, T0 + timedelta(minutes=30))
    db.alert_resolved("t2", T0 + timedelta(minutes=32), "retirados")
    rot = operation_metrics(db)["rotation"]
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


def test_a_database_from_the_m1_branch_keeps_its_columns(tmp_path):
    """Una base de antes de Alembic que ya tenía las columnas de M-1 (la rama las
    agregaba a mano) se migra sin crearlas de nuevo ni perder lo guardado."""
    from tests.test_db import OLD_SCHEMA

    path = tmp_path / "servio.db"
    with sqlite3.connect(path) as conn:
        conn.executescript(OLD_SCHEMA)
        conn.executescript(
            """
            ALTER TABLE avisos ADD COLUMN motivo_cierre TEXT;
            ALTER TABLE platos ADD COLUMN precio REAL;
            ALTER TABLE platos_servidos ADD COLUMN ultimo_fill_ratio REAL;
            ALTER TABLE platos_servidos ADD COLUMN retirado_at TEXT;
            CREATE TABLE estados_plato (id INTEGER PRIMARY KEY,
                plato_servido_id INTEGER NOT NULL REFERENCES platos_servidos(id),
                estado TEXT NOT NULL, fill_ratio REAL, desde TEXT NOT NULL);
            UPDATE platos_servidos SET ultimo_fill_ratio = 0.4;
            INSERT INTO estados_plato (plato_servido_id, estado, fill_ratio, desde)
                VALUES (1, 'comiendo', 0.4, '2026-10-18T20:05:00Z');
            """
        )
    db = _db(tmp_path)
    try:
        [version] = db.query("SELECT version_num FROM alembic_version")
        assert version[0] == "0005"
        [row] = db.query("SELECT precio FROM platos WHERE id = 'lomo'")
        assert row["precio"] == 890
        [served] = db.query("SELECT ultimo_fill_ratio FROM platos_servidos")
        assert served["ultimo_fill_ratio"] == 0.4
        [states] = db.query("SELECT COUNT(*) AS n FROM estados_plato")
        assert states["n"] == 1
    finally:
        db.close()


def test_live_readings_give_pace_and_leftover(tmp_path):
    from servio_vision.alerts.config import AlertConfig
    from servio_vision.alerts.processor import AlertProcessor
    from tests.test_alert_engine import FILL, obs
    from tests.test_feedback import FakeTelegram

    db = Database(tmp_path / "servio.db")
    p = AlertProcessor(
        AlertConfig(interval=30, t1=60, t2=600, reminder=600),
        FakeTelegram(),
        tmp_path / "eventos.jsonl",
        Restaurant.load(),
        db,
    )
    cookies = ("T1-p1", "centro")
    donas = ("T1-p2", "izquierda")
    try:
        for s, state in ((0, "lleno"), (30, "lleno"), (60, "comiendo")):
            p.process(
                obs(s, (cookies[0], state, cookies[1]), (donas[0], "lleno", donas[1]))
            )
        for s in range(90, 301, 30):  # se llevan las donas llenas; las cookies, vacías
            p.process(obs(s, (cookies[0], "vacio", cookies[1])))
        [states] = db.query("SELECT COUNT(*) AS n FROM estados_plato")
        ops = operation_metrics(db)
    finally:
        p.close()
    assert states["n"] == 4  # cookies lleno, comiendo, vacío; donas lleno
    [(dish, plates, minutes)] = ops["pace"]
    assert (dish, plates) == ("Cookies con chips de chocolate", 1)
    assert minutes == pytest.approx(1.5)
    assert ops["leftover"] == [("Donas glaseadas", 1, FILL["lleno"])]


def test_waiting_for_next_dish_load_and_peaks(tmp_path):
    db = _db(tmp_path)
    db.served("T1", "cookies", "T1-p1", T0)
    db.alert_opened("t1", "mesa_terminada", "T1", None, T0 + timedelta(minutes=10))
    db.alert_resolved("t1", T0 + timedelta(minutes=16), "servido")
    db.served("T1", "donas", "T1-p2", T0 + timedelta(minutes=16))
    db.alert_opened("t2", "mesa_terminada", "T1", None, T0 + timedelta(minutes=40))
    db.alert_resolved("t2", T0 + timedelta(minutes=43), "retirados")
    ops = operation_metrics(db)
    assert ops["next_dish"] == [("Juan", 1, ops["next_dish"][0][2])]
    assert round(ops["next_dish"][0][2]) == 6
    assert ops["load"][0][:2] == ("Juan", 1.0)
    assert [(slot, n) for slot, n, _ in ops["peaks"]] == [("21:00", 1), ("21:30", 1)]
    db.close()


def test_camera_times_are_local_time(tmp_path):
    db = _db(tmp_path)
    db.served("T1", "cookies", "T1-p1", T0)
    db.alert_opened("t1", "mesa_terminada", "T1", None, T0 + timedelta(minutes=20))
    db.alert_resolved("t1", T0 + timedelta(minutes=22), "retirados")
    [(_, start, _)] = table_visits(db)
    assert start == datetime(2026, 10, 18, 21, 0)  # cena del 18, no almuerzo del 19
    assert operation_metrics(db)["rotation"]["by_kind"].keys() == {"cena"}
    db.close()


def test_a_visit_left_open_is_not_closed_days_later(tmp_path):
    db = _db(tmp_path)
    db.served("T1", "cookies", "T1-p1", T0)  # nadie la retiró
    later = T0 + timedelta(minutes=VISIT_GAP_MIN + 60)
    db.served("T1", "donas", "T1-p2", later)
    db.alert_opened("t1", "mesa_terminada", "T1", None, later + timedelta(minutes=30))
    db.alert_resolved("t1", later + timedelta(minutes=31), "retirados")
    [(_, start, _)] = table_visits(db)
    assert start == datetime(2026, 10, 19, 0, 0)  # empieza con las donas
    assert round(operation_metrics(db)["rotation"]["minutes"]) == 31
    db.close()


def test_a_plate_that_changes_id_is_the_same_served_plate(tmp_path):
    from servio_vision.alerts.config import AlertConfig
    from servio_vision.alerts.processor import AlertProcessor
    from tests.test_alert_engine import obs
    from tests.test_feedback import FakeTelegram

    db = Database(tmp_path / "servio.db")
    p = AlertProcessor(
        AlertConfig(interval=30, t1=60, t2=600, reminder=600),
        FakeTelegram(),
        tmp_path / "eventos.jsonl",
        Restaurant.load(),
        db,
    )
    try:
        p.process(obs(0, ("T1-p1", "lleno", "centro")))
        p.process(obs(30, ("T1-p1", "comiendo", "centro")))
        p.process(
            obs(60, ("T1-p7", "comiendo", "centro"))
        )  # el tracker le cambió el id
        p.process(obs(90, ("T1-p7", "vacio", "centro")))
        rows = db.query("SELECT ultimo_fill_ratio, retirado_at FROM platos_servidos")
        [changes] = db.query("SELECT COUNT(*) AS n FROM estados_plato")
    finally:
        p.close()
    assert len(rows) == 1 and rows[0]["retirado_at"] is None
    assert changes["n"] == 3  # lleno, comiendo, vacío: el cambio de id no cuenta


def test_a_reading_without_fill_ratio_does_not_keep_the_old_one(tmp_path):
    db = _db(tmp_path)
    served = db.served("T1", "cookies", "T1-p1", T0)
    db.plate_readings([(served, "lleno", 0.9, True)], [], T0)
    db.plate_readings([(served, "vacio", None, True)], [], T0 + timedelta(minutes=9))
    [row] = db.query("SELECT ultimo_fill_ratio FROM platos_servidos")
    assert row["ultimo_fill_ratio"] is None
    db.close()
