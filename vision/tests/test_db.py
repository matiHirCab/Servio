"""Base relacional: lo que pasa en vivo queda en las tablas y las métricas lo leen."""

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

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
