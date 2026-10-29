"""Base relacional: lo que pasa en vivo queda en las tablas y las métricas lo leen."""

import os
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from servio_vision.db import Database
from servio_vision.restaurant import Restaurant

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import metricas

T0 = datetime(2026, 10, 18, 20, 0, tzinfo=UTC)


def test_live_alert_and_answer_are_related(tmp_path):
    db = Database(tmp_path / "servio.db")
    restaurant = Restaurant.load()
    db.load_restaurant(restaurant)
    served = db.served("T1", "donas", "T1-p2", T0)
    db.alert_opened("a1", "plato_lleno_mucho", "T1", served, T0 + timedelta(minutes=1))
    db.feedback(
        {
            "alert_id": "a1",
            "table_id": "T1",
            "answer": "Muy dulces",
            "free_text": False,
            "answered_by": "jess",
            "seconds_to_answer": 20,
            "ts": "2026-10-18T20:01:20Z",
        }
    )
    db.alert_resolved("a1", T0 + timedelta(minutes=3))
    [row] = db.query(
        """SELECT p.nombre, m.nombre AS mozo, r.texto, r.segundos, a.resuelto_at
             FROM respuestas r
             JOIN avisos a ON a.id = r.aviso_id
             JOIN platos_servidos ps ON ps.id = a.plato_servido_id
             JOIN platos p ON p.id = ps.plato_id
             JOIN mozos m ON m.id = r.mozo_id"""
    )
    assert tuple(row) == (
        "Donas glaseadas",
        "Juan",
        "Muy dulces",
        20.0,
        "2026-10-18T20:03:00Z",
    )
    m = metricas.compute(db)
    assert m["dishes"] == [("Donas glaseadas", 1.0, "Muy dulces")]
    assert m["answer"] == [("Juan", 20.0)]
    assert m["live_answers"] == 1
    db.close()


def test_history_imports_once(tmp_path):
    db = Database(tmp_path / "servio.db")
    restaurant = Restaurant.load()
    db.load_restaurant(restaurant)
    history = Path(metricas.HISTORY)
    first = db.import_history(history, restaurant)
    assert first > 0
    assert db.import_history(history, restaurant) == 0  # no se duplica
    m = metricas.compute(db)
    assert m["dishes"][0][0] == "Risotto de hongos"  # el patrón simulado
    assert m["attend"][0][0] == "Martín"
    db.close()


# --- A-6: migraciones, local_id, Postgres opcional ---

OLD_SCHEMA = """
CREATE TABLE mozos (id TEXT PRIMARY KEY, nombre TEXT NOT NULL);
CREATE TABLE mesas (id TEXT PRIMARY KEY, nombre TEXT NOT NULL, mozo_id TEXT REFERENCES mozos(id));
CREATE TABLE platos (id TEXT PRIMARY KEY, nombre TEXT NOT NULL UNIQUE);
CREATE TABLE respuestas_tipicas (plato_id TEXT NOT NULL REFERENCES platos(id), orden INTEGER NOT NULL, texto TEXT NOT NULL, PRIMARY KEY (plato_id, orden));
CREATE TABLE platos_servidos (id INTEGER PRIMARY KEY, mesa_id TEXT NOT NULL REFERENCES mesas(id), plato_id TEXT REFERENCES platos(id), track_id TEXT, servido_at TEXT NOT NULL, origen TEXT NOT NULL);
CREATE TABLE avisos (id TEXT PRIMARY KEY, tipo TEXT NOT NULL, mesa_id TEXT NOT NULL REFERENCES mesas(id), mozo_id TEXT REFERENCES mozos(id), plato_servido_id INTEGER REFERENCES platos_servidos(id), abierto_at TEXT NOT NULL, resuelto_at TEXT, origen TEXT NOT NULL);
CREATE TABLE respuestas (id INTEGER PRIMARY KEY, aviso_id TEXT NOT NULL REFERENCES avisos(id), mozo_id TEXT REFERENCES mozos(id), respondido_por TEXT, texto TEXT NOT NULL, escrita INTEGER NOT NULL, segundos REAL, respondido_at TEXT NOT NULL);
INSERT INTO mozos VALUES ('juan', 'Juan');
INSERT INTO mesas VALUES ('T1', 'Mesa 1', 'juan');
INSERT INTO platos VALUES ('donas', 'Donas glaseadas');
INSERT INTO platos_servidos (mesa_id, plato_id, track_id, servido_at, origen) VALUES ('T1', 'donas', 'T1-p1', '2026-10-18T20:00:00Z', 'camara');
INSERT INTO avisos VALUES ('T1-mesa_terminada-1', 'mesa_terminada', 'T1', 'juan', NULL, '2026-10-18T20:10:00Z', '2026-10-18T20:12:00Z', 'historial');
INSERT INTO avisos VALUES ('T1-plato_lleno_mucho-2', 'plato_lleno_mucho', 'T1', 'juan', 1, '2026-10-18T20:20:00Z', NULL, 'camara');
INSERT INTO respuestas (aviso_id, mozo_id, respondido_por, texto, escrita, segundos, respondido_at) VALUES ('T1-plato_lleno_mucho-2', 'juan', 'jess', 'Muy dulces', 0, 20, '2026-10-18T20:21:00Z');
"""


def test_a_database_from_before_alembic_upgrades_itself(tmp_path):
    """Hecho cuando: una base de la versión anterior se actualiza sola al arrancar,
    sin perder filas. La vieja no tenía motivo_cierre ni local_id."""
    import sqlite3

    path = tmp_path / "servio.db"
    with sqlite3.connect(path) as raw:
        raw.executescript(OLD_SCHEMA)
    db = Database(path)
    try:
        [version] = db.query("SELECT version_num FROM alembic_version")
        assert version[0] == "0004"
        rows = db.query(
            "SELECT local_id, id, motivo_cierre FROM avisos ORDER BY abierto_at"
        )
        assert [tuple(r) for r in rows] == [
            ("local", "T1-mesa_terminada-1", "retirados"),  # historial: retirados
            ("local", "T1-plato_lleno_mucho-2", None),
        ]
        [(answer,)] = db.query("SELECT local_id || ':' || aviso_id FROM respuestas")
        assert answer == "local:T1-plato_lleno_mucho-2"
        [(served,)] = db.query("SELECT local_id FROM platos_servidos")
        assert served == "local"
        # y sigue funcionando: la segunda apertura no migra nada ni rompe
        db.alert_resolved("T1-plato_lleno_mucho-2", T0)
    finally:
        db.close()
    db = Database(path)
    db.close()


def test_two_houses_write_the_same_database_without_clashing(tmp_path):
    """Hecho cuando: dos casas a la vez en la misma base. El id de un aviso es
    `{mesa}-{tipo}-{segundo}`: con la misma Mesa 1 en el mismo segundo, cada casa
    guarda el suyo."""
    path = tmp_path / "compartida.db"
    restaurant = Restaurant.load()
    a, b = Database(path, local_id="casa-a"), Database(path, local_id="casa-b")
    try:
        a.load_restaurant(restaurant)
        for db in (a, b):
            served = db.served("T1", "donas", "T1-p1", T0)
            db.alert_opened(
                "T1-plato_lleno_mucho-1", "plato_lleno_mucho", "T1", served, T0
            )
            db.feedback(
                {
                    "alert_id": "T1-plato_lleno_mucho-1",
                    "table_id": "T1",
                    "answer": "Muy dulces" if db is a else "Está todo bien",
                    "free_text": False,
                    "answered_by": "jess",
                    "seconds_to_answer": 10,
                    "ts": "2026-10-18T20:01:00Z",
                }
            )
        a.alert_resolved("T1-plato_lleno_mucho-1", T0 + timedelta(minutes=2))
        rows = a.query("SELECT local_id, resuelto_at FROM avisos ORDER BY local_id")
        assert [tuple(r) for r in rows] == [
            ("casa-a", "2026-10-18T20:02:00Z"),
            ("casa-b", None),  # cerrar el de A no toca el de B
        ]
        # cada respuesta queda con el aviso de su casa
        rows = a.query(
            "SELECT r.local_id, r.texto FROM respuestas r"
            " JOIN avisos av ON av.id = r.aviso_id AND av.local_id = r.local_id"
            " ORDER BY r.local_id"
        )
        assert [tuple(r) for r in rows] == [
            ("casa-a", "Muy dulces"),
            ("casa-b", "Está todo bien"),
        ]
        m = metricas.compute(a)
        assert m["dishes"] == [("Donas glaseadas", 1.0, "Muy dulces")]
        assert m["live_answers"] == 1  # las respuestas en vivo son solo de esta casa
    finally:
        a.close()
        b.close()


def test_render_adapts_the_elapsed_time_to_the_engine(tmp_path):
    db = Database(tmp_path / "servio.db")
    try:
        sql = db.render(metricas.SQL_ATTEND)
        assert "julianday(a.resuelto_at)" in sql and "{elapsed" not in sql
        db.dialect = "postgresql"
        assert "EXTRACT(EPOCH FROM" in db.render(metricas.SQL_ATTEND)
    finally:
        db.close()


def test_engine_url_prefers_psycopg_for_postgres(tmp_path):
    from servio_vision.db import engine_url

    assert engine_url(tmp_path / "x.db", None) == f"sqlite:///{tmp_path / 'x.db'}"
    assert engine_url(None, "postgresql://u:p@h/db") == "postgresql+psycopg://u:p@h/db"
    assert engine_url(None, "postgres://u:p@h/db") == "postgresql+psycopg://u:p@h/db"
    assert engine_url(None, "postgresql+psycopg://u:p@h/db").startswith(
        "postgresql+psycopg://"
    )


@pytest.mark.skipif(
    not os.getenv("SERVIO_TEST_DATABASE_URL"),
    reason="SERVIO_TEST_DATABASE_URL: un Postgres de prueba (Neon/Supabase)",
)
def test_postgres_end_to_end(tmp_path):
    """Hecho cuando: `servio metricas` lee de Postgres. Con una base de prueba:
    migra, escribe en vivo, importa el historial y calcula las métricas."""
    db = Database(tmp_path / "out.db", url=os.environ["SERVIO_TEST_DATABASE_URL"])
    try:
        restaurant = Restaurant.load()
        db.load_restaurant(restaurant)
        served = db.served("T1", "donas", f"T1-p{int(T0.timestamp())}", T0)
        db.alert_opened(
            f"T1-plato_lleno_mucho-{int(T0.timestamp())}",
            "plato_lleno_mucho",
            "T1",
            served,
            T0,
        )
        db.import_history(Path(metricas.HISTORY), restaurant)
        m = metricas.compute(db)
        assert m["dishes"] and m["attend"]
    finally:
        db.close()


def test_foreign_keys_stay_on_after_migrating(tmp_path):
    """Migrar apaga las FK de SQLite un momento; la conexión vuelve al pool con
    las FK prendidas: una fila que apunta a una mesa que no existe se rechaza."""
    import sqlalchemy

    db = Database(tmp_path / "servio.db")
    try:
        [(on,)] = db.query("PRAGMA foreign_keys")
        assert on == 1
        with pytest.raises(sqlalchemy.exc.IntegrityError):
            db._write(
                sqlalchemy.text(
                    "INSERT INTO avisos (local_id, id, tipo, mesa_id, abierto_at, origen)"
                    " VALUES ('local', 'x', 'mesa_terminada', 'NOPE', 't', 'camara')"
                )
            )
    finally:
        db.close()


def test_live_metrics_are_only_this_house(tmp_path):
    """Lo que pasó en vivo (último retiro, respuestas de hoy) es de esta casa; la
    otra casa no aparece en mi /metricas."""
    path = tmp_path / "compartida.db"
    restaurant = Restaurant.load()
    a, b = Database(path, local_id="casa-a"), Database(path, local_id="casa-b")
    try:
        a.load_restaurant(restaurant)
        b.alert_opened("T1-mesa_terminada-1", "mesa_terminada", "T1", None, T0)
        b.alert_resolved("T1-mesa_terminada-1", T0 + timedelta(seconds=90), "retirados")
        assert metricas.compute(a)["last_pickup"] is None
        mozo, mesa, seconds = metricas.compute(b)["last_pickup"]
        assert (mozo, mesa) == ("Juan", "Mesa 1") and seconds == pytest.approx(
            90, abs=0.01
        )
    finally:
        a.close()
        b.close()


def test_restaurant_reload_drops_answers_that_went_away(tmp_path):
    db = Database(tmp_path / "servio.db")
    try:
        restaurant = Restaurant.load()
        db.load_restaurant(restaurant)
        [(before,)] = db.query("SELECT COUNT(*) FROM respuestas_tipicas")
        did, dish = next(iter(restaurant.dishes.items()))
        restaurant.dishes[did] = replace(dish, answers=dish.answers[:1])
        db.load_restaurant(restaurant)  # otra vez, con menos opciones: no choca
        [(after,)] = db.query("SELECT COUNT(*) FROM respuestas_tipicas")
        assert after < before
    finally:
        db.close()


def test_query_takes_named_params_and_rejects_the_rest(tmp_path):
    db = Database(tmp_path / "servio.db")
    try:
        with pytest.raises(TypeError):
            db.query("SELECT * FROM mesas WHERE id = ?", ("T1",))  # solo con nombre
        db.load_restaurant(Restaurant.load())
        [row] = db.query("SELECT id FROM mesas WHERE id = :mesa", {"mesa": "T1"})
        assert row["id"] == "T1"
        with pytest.raises(KeyError):
            row["typo"]
    finally:
        db.close()


def test_history_without_served_plates_imports_the_alerts(tmp_path):
    """Un historial solo con mesas terminadas no tiene platos servidos: igual entra."""
    history = tmp_path / "historial.jsonl"
    history.write_text(
        '{"fecha": "2026-10-01", "servicio": "cena", "mesa": "T1", "mozo": "Juan",'
        ' "tipo": "mesa_terminada", "minutos_hasta_atender": 3}\n',
        encoding="utf-8",
    )
    db = Database(tmp_path / "servio.db")
    try:
        restaurant = Restaurant.load()
        db.load_restaurant(restaurant)
        assert db.import_history(history, restaurant) == 1
        [(local, motivo)] = db.query("SELECT local_id, motivo_cierre FROM avisos")
        assert (local, motivo) == ("historial", "retirados")
    finally:
        db.close()
