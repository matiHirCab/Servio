"""Tests del motor de reglas (MAT-3): replay de fixtures y casos sueltos."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from servio_vision.alerts.config import load_config
from servio_vision.alerts.engine import EngineResult, evaluate, table_name
from servio_vision.alerts.models import Alert, TableObservation
from servio_vision.alerts.state import TableState, empty_table, update_table

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
SCENARIOS = [
    "mesa_completa",
    "plato_lleno_mucho",
    "id_cambiado",
    "plato_vacio",
    "mesa_terminada_recordatorios",
]
START = datetime(2026, 10, 17, 20, 0, 0, tzinfo=UTC)
FILL = {"lleno": 0.9, "comiendo": 0.55, "por_terminar": 0.2, "vacio": 0.02}
PROD = load_config(interval=30, demo=False, env={})


def at(seconds: int) -> datetime:
    return START + timedelta(seconds=seconds)


def load(name: str) -> list[TableObservation]:
    path = FIXTURES / "observations" / f"{name}.jsonl"
    lines = path.read_text(encoding="utf-8").splitlines()
    return [
        TableObservation.model_validate_json(line) for line in lines if line.strip()
    ]


def replay(observations, config=PROD) -> list[dict]:
    """Corre estado + motor lectura por lectura y devuelve los eventos, como el log."""
    start = observations[0].ts
    tables: dict[str, TableState] = {}
    open_alerts: dict[str, Alert] = {}
    events = []
    for o in observations:
        table = update_table(tables.get(o.table_id, empty_table(o.table_id)), o)
        tables[o.table_id] = table
        result = evaluate(table, o.ts, config, open_alerts.values())
        second = int((o.ts - start).total_seconds())
        for alert in result.opened:
            open_alerts[alert.id] = alert
            events.append(event(second, "alert_opened", alert))
        for reminder in result.reminded:
            open_alerts[reminder.alert.id] = reminder.alert
            events.append(event(second, "alert_reminder", reminder.alert))
        for alert in result.resolved:
            del open_alerts[alert.id]
            events.append(event(second, "alert_resolved", alert))
    return events


def event(second: int, name: str, alert: Alert) -> dict:
    return {
        "at_seconds": second,
        "event": name,
        "type": alert.type,
        "table_id": alert.table_id,
    }


def obs(seconds: int, *plates: tuple[str, str, str], people: bool = True):
    """Observación de T1 con platos (track_id, state, position)."""
    return TableObservation(
        schema_version=1,
        camera_id="cam-1",
        ts=at(seconds),
        table_id="T1",
        people_present=people,
        items=[
            {
                "track_id": tid,
                "state": state,
                "fill_ratio": FILL[state],
                "confidence": 0.9,
                "position": pos,
            }
            for tid, state, pos in plates
        ],
    )


def every(seconds_from: int, seconds_to: int, *plates, people: bool = True):
    """Una observación cada 30 s entre dos tiempos, con los mismos platos."""
    return [
        obs(s, *plates, people=people) for s in range(seconds_from, seconds_to + 1, 30)
    ]


# --- fixtures ---


@pytest.mark.parametrize("name", SCENARIOS)
def test_replay_matches_expected(name: str):
    expected = json.loads(
        (FIXTURES / "expected" / f"{name}.alerts.json").read_text("utf-8")
    )
    assert replay(load(name)) == expected


# --- casos sueltos ---

VACIO = ("T1-p1", "vacio", "centro")
# otro plato que se sigue comiendo: con él, un plato vacío es plato_vacio (sin él, la
# mesa entera terminó y avisa mesa_terminada)
COMIENDO = ("T1-p9", "comiendo", "izquierda")
LLENO = ("T1-p1", "lleno", "centro")


def test_one_alert_per_table_and_type():
    # Un plato vacío 5 min: un solo aviso abierto (más sus recordatorios), sin duplicar.
    events = replay(every(0, 300, VACIO, COMIENDO))
    opened = [
        (e["at_seconds"], e["type"]) for e in events if e["event"] == "alert_opened"
    ]
    assert opened == [(60, "plato_vacio")]


def test_reminders_at_r_and_2r_and_no_more():
    # plato vacío desde 0 s: abre a los 60 s, recuerda a 240 s y 420 s, y no más.
    events = replay(every(0, 1200, VACIO, COMIENDO))
    vacio = [
        (e["at_seconds"], e["event"]) for e in events if e["type"] == "plato_vacio"
    ]
    assert vacio == [
        (60, "alert_opened"),
        (240, "alert_reminder"),
        (420, "alert_reminder"),
    ]


def test_full_plate_does_not_open_without_people():
    events = replay(every(0, 900, LLENO, people=False))
    assert not [e for e in events if e["type"] == "plato_lleno_mucho"]


def test_empty_plate_opens_without_people():
    events = replay(every(0, 90, VACIO, COMIENDO, people=False))
    assert ("plato_vacio", "alert_opened") in [(e["type"], e["event"]) for e in events]


def test_one_reading_without_people_does_not_close_full_plate():
    readings = (
        every(0, 600, LLENO) + [obs(630, LLENO, people=False)] + every(660, 690, LLENO)
    )
    events = [e for e in replay(readings) if e["type"] == "plato_lleno_mucho"]
    assert [e["event"] for e in events] == ["alert_opened"]


def test_two_readings_without_people_close_full_plate():
    readings = every(0, 600, LLENO) + every(630, 660, LLENO, people=False)
    events = [e for e in replay(readings) if e["type"] == "plato_lleno_mucho"]
    assert [(e["at_seconds"], e["event"]) for e in events] == [
        (600, "alert_opened"),
        (660, "alert_resolved"),
    ]


def test_provisional_plate_does_not_count():
    # Un id nuevo vacío en la posición de un plato lleno que sigue: no abre nada
    # ni cambia el estado de la mesa en esa lectura.
    state = empty_table("T1")
    state = update_table(state, obs(0, ("T1-p1", "comiendo", "centro")))
    state = update_table(
        state, obs(30, ("T1-p1", "comiendo", "centro"), ("T1-p9", "vacio", "centro"))
    )
    result = evaluate(state, at(30), PROD, [])
    assert result == EngineResult([], [], [])


def test_demo_thresholds_open_earlier():
    demo = load_config(interval=30, demo=True, env={})  # 2 × 30 = 60 s
    events = replay(every(0, 120, LLENO), config=demo)
    assert ("plato_lleno_mucho", "alert_opened", 60) in [
        (e["type"], e["event"], e["at_seconds"]) for e in events
    ]


def test_evaluate_does_not_modify_inputs():
    state = empty_table("T1")
    for o in every(0, 120, VACIO, COMIENDO):
        state = update_table(state, o)
    alert = evaluate(state, at(60), PROD, []).opened[0]
    snapshot = (alert.model_dump(), dict(state.plates))
    result = evaluate(state, at(240), PROD, [alert])
    assert result.reminded[0].alert.reminders_sent == 1
    assert (alert.model_dump(), dict(state.plates)) == snapshot


def test_ignores_open_alerts_of_other_tables():
    state = empty_table("T1")
    for o in every(0, 60, VACIO, COMIENDO):
        state = update_table(state, o)
    other = (
        evaluate(state, at(60), PROD, [])
        .opened[0]
        .model_copy(update={"table_id": "T2", "id": "T2-plato_vacio-x"})
    )
    result = evaluate(state, at(60), PROD, [other])
    assert [a.table_id for a in result.opened] == ["T1"]
    assert result.resolved == []


def test_messages():
    state = empty_table("T1")
    for o in every(0, 600, LLENO):
        state = update_table(state, o)
    (alert,) = evaluate(state, at(600), PROD, []).opened
    assert (
        alert.message == "❓ Mesa 1: un plato sigue lleno hace 10 min. ¿Está todo bien?"
    )
    assert table_name("T12") == "Mesa 12"
    assert table_name("barra") == "barra"


# --- mesa_terminada (reemplaza a mesa_por_terminar) ---

POR_TERMINAR = ("T1-p1", "por_terminar", "centro")
OTRO_VACIO = ("T1-p9", "vacio", "izquierda")


def test_por_terminar_does_not_alert():
    events = replay(every(0, 600, POR_TERMINAR, COMIENDO))
    assert events == []


def test_table_finished_opens_once_and_closes_only_when_plates_are_removed():
    readings = (
        every(0, 60, POR_TERMINAR, COMIENDO)
        + every(90, 300, VACIO, OTRO_VACIO)  # la mesa terminó a los 90 s
        + every(330, 390)  # retiraron los platos (2 lecturas sin platos: libre)
    )
    events = [(e["at_seconds"], e["event"], e["type"]) for e in replay(readings)]
    assert events[0] == (90, "alert_opened", "mesa_terminada")
    assert ("alert_resolved", "mesa_terminada") in [(e, t) for _, e, t in events]
    resolved_at = next(s for s, e, _ in events if e == "alert_resolved")
    assert resolved_at >= 330  # no se cierra mientras los platos siguen en la mesa
    assert not [
        t for _, _, t in events if t == "plato_vacio"
    ]  # avisa la mesa, no el plato


def test_new_dish_closes_table_finished():
    readings = every(0, 60, VACIO) + every(90, 150, ("T1-p2", "lleno", "centro"))
    events = [(e["at_seconds"], e["event"], e["type"]) for e in replay(readings)]
    assert events[:2] == [
        (0, "alert_opened", "mesa_terminada"),
        (90, "alert_resolved", "mesa_terminada"),
    ]


def test_empty_plate_alert_stays_open_when_the_rest_finishes():
    # un plato vacío con otro comiendo → plato_vacio; después termina el otro: se suma
    # mesa_terminada, y plato_vacio no se "resuelve" solo porque la mesa terminó.
    readings = every(0, 90, VACIO, COMIENDO) + every(120, 180, VACIO, OTRO_VACIO)
    events = [(e["at_seconds"], e["event"], e["type"]) for e in replay(readings)]
    assert (60, "alert_opened", "plato_vacio") in events
    assert (120, "alert_opened", "mesa_terminada") in events
    assert not [e for e in events if e[1] == "alert_resolved"]
