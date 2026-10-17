"""Tests del estado por plato, la reconciliación de ids y table_status (MAT-2)."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from servio_vision.alerts.models import TableObservation
from servio_vision.alerts.state import (
    TableState,
    empty_table,
    table_status,
    update_table,
)

OBSERVATIONS = Path(__file__).resolve().parents[2] / "fixtures" / "observations"
START = datetime(2026, 10, 17, 20, 0, 0, tzinfo=UTC)
FILL = {"lleno": 0.9, "comiendo": 0.55, "por_terminar": 0.2, "vacio": 0.02}


def load(name: str) -> list[TableObservation]:
    lines = (OBSERVATIONS / f"{name}.jsonl").read_text(encoding="utf-8").splitlines()
    return [
        TableObservation.model_validate_json(line) for line in lines if line.strip()
    ]


def replay(observations: list[TableObservation]) -> list[TableState]:
    """Estado después de cada observación."""
    state = empty_table(observations[0].table_id)
    states = []
    for obs in observations:
        state = update_table(state, obs)
        states.append(state)
    return states


def obs(seconds: int, *plates: tuple[str, str, str]) -> TableObservation:
    """Observación de T1 a START + seconds con platos (track_id, state, position)."""
    return TableObservation(
        schema_version=1,
        camera_id="cam-1",
        ts=START + timedelta(seconds=seconds),
        table_id="T1",
        items=[
            {
                "track_id": tid,
                "state": state,
                "fill_ratio": FILL[state],
                "confidence": 0.9,
                "position": position,
            }
            for tid, state, position in plates
        ],
    )


def run(*observations: TableObservation) -> TableState:
    state = empty_table("T1")
    for o in observations:
        state = update_table(state, o)
    return state


# --- fixtures ---


def test_id_cambiado_keeps_plate_and_times():
    states = replay(load("id_cambiado"))
    for state in states:
        assert "T1-p4" not in state.plates  # nunca es un plato propio
        assert list(state.plates).count("T1-p1") == 1
    swap = states[6]  # 180 s: llegan T1-p1 (retenido) y T1-p4 juntos
    assert set(swap.plates) == {"T1-p1", "T1-p2"}
    final = states[-1]
    assert set(final.plates) == {"T1-p1"}  # T1-p2 dejó de venir a los 300 s
    plate = final.plates["T1-p1"]
    assert plate.first_seen == START
    assert plate.state_since == START
    assert final.aliases == {"T1-p4": "T1-p1"}


def test_id_cambiado_removes_plate_when_it_stops_coming():
    states = replay(load("id_cambiado"))
    assert "T1-p2" in states[9].plates  # 270 s
    assert "T1-p2" not in states[10].plates  # 300 s


def test_mesa_completa_removes_plates_and_starts_new_one():
    states = replay(load("mesa_completa"))
    by_second = {int((s.last_ts - START).total_seconds()): s for s in states}
    assert set(by_second[480].plates) == {"T1-p1", "T1-p2"}
    assert by_second[510].plates == {}
    new = by_second[720].plates["T1-p3"]
    assert new.first_seen == new.state_since == START + timedelta(seconds=720)


def test_mesa_completa_state_since_changes_only_with_state():
    states = replay(load("mesa_completa"))
    plate_at = {
        int((s.last_ts - START).total_seconds()): s.plates.get("T1-p1") for s in states
    }
    assert plate_at[150].state_since == START + timedelta(seconds=90)  # lleno desde 90
    assert plate_at[240].state_since == START + timedelta(
        seconds=180
    )  # comiendo desde 180
    assert plate_at[480].state_since == START + timedelta(
        seconds=360
    )  # vacio desde 360
    assert plate_at[480].first_seen == START + timedelta(seconds=90)


def test_mesa_completa_table_status_sequence():
    statuses = [
        table_status(p.state for p in s.plates.values())
        for s in replay(load("mesa_completa"))
    ]
    transitions = [s for i, s in enumerate(statuses) if i == 0 or s != statuses[i - 1]]
    assert transitions == [
        "libre",
        "servida",
        "comiendo",
        "por_terminar",
        "terminada",
        "libre",
        "servida",
    ]


# --- reconciliación ---


def test_new_id_in_same_position_is_same_plate_when_old_disappears():
    state = run(
        obs(0, ("T1-p1", "lleno", "centro")),
        obs(30, ("T1-p7", "comiendo", "centro")),
    )
    assert set(state.plates) == {"T1-p1"}
    assert state.plates["T1-p1"].first_seen == START
    assert state.plates["T1-p1"].state == "comiendo"
    assert state.aliases == {"T1-p7": "T1-p1"}


def test_double_reading_gives_one_plate_with_fresh_state():
    state = run(
        obs(0, ("T1-p1", "lleno", "centro")),
        obs(30, ("T1-p1", "lleno", "centro"), ("T1-p7", "comiendo", "centro")),
    )
    assert set(state.plates) == {"T1-p1"}
    assert state.plates["T1-p1"].state == "comiendo"  # gana la lectura del id nuevo


def test_no_match_when_two_previous_plates_share_position():
    state = run(
        obs(0, ("T1-p1", "lleno", "centro"), ("T1-p2", "lleno", "centro")),
        obs(30, ("T1-p7", "lleno", "centro")),
    )
    assert set(state.plates) == {"T1-p7"}
    assert state.plates["T1-p7"].first_seen == START + timedelta(seconds=30)
    assert state.aliases == {}


def test_no_match_when_two_new_ids_share_position():
    state = run(
        obs(0, ("T1-p1", "lleno", "centro")),
        obs(30, ("T1-p7", "lleno", "centro"), ("T1-p8", "lleno", "centro")),
    )
    assert set(state.plates) == {"T1-p7", "T1-p8"}


def test_new_id_in_other_position_is_new_plate():
    state = run(
        obs(0, ("T1-p1", "lleno", "centro")),
        obs(30, ("T1-p1", "lleno", "centro"), ("T1-p2", "lleno", "izquierda")),
    )
    assert set(state.plates) == {"T1-p1", "T1-p2"}
    assert state.plates["T1-p2"].first_seen == START + timedelta(seconds=30)


def test_removed_plate_drops_its_aliases():
    state = run(
        obs(0, ("T1-p1", "lleno", "centro")),
        obs(30, ("T1-p7", "lleno", "centro")),
        obs(60),
    )
    assert state.plates == {}
    assert state.aliases == {}


def test_position_change_is_followed():
    state = run(
        obs(0, ("T1-p1", "lleno", "centro")),
        obs(30, ("T1-p1", "lleno", "izquierda")),
        obs(60, ("T1-p9", "lleno", "izquierda")),
    )
    assert set(state.plates) == {"T1-p1"}  # empareja con la posición más reciente


# --- pureza y validación ---


def test_update_does_not_modify_previous_state():
    before = run(obs(0, ("T1-p1", "lleno", "centro")))
    snapshot = (dict(before.plates), dict(before.aliases), before.last_ts)
    update_table(before, obs(30, ("T1-p7", "comiendo", "centro")))
    assert (dict(before.plates), dict(before.aliases), before.last_ts) == snapshot


def test_rejects_observation_of_other_table():
    with pytest.raises(ValueError):
        update_table(empty_table("T2"), obs(0))


def test_rejects_going_back_in_time():
    state = run(obs(30))
    with pytest.raises(ValueError):
        update_table(state, obs(0))


# --- table_status ---


@pytest.mark.parametrize(
    ("states", "status"),
    [
        ([], "libre"),
        (["vacio", "vacio"], "terminada"),
        (["por_terminar", "vacio", "lleno"], "por_terminar"),
        (["por_terminar", "lleno"], "por_terminar"),  # 50 % justo: gana por_terminar
        (["lleno", "comiendo"], "servida"),
        (["lleno", "comiendo", "comiendo"], "comiendo"),
        (["comiendo"], "comiendo"),
    ],
)
def test_table_status(states, status):
    assert table_status(states) == status


def test_id_swap_through_real_smoother():
    # Integración con smoothing.py (JES-5): el clasificador cambia p1 → p4 en la
    # misma posición y alerts tiene que seguir viendo un solo plato desde el inicio.
    from servio_vision.schemas import Item
    from servio_vision.smoothing import TableSmoother

    smoother = TableSmoother()
    state = empty_table("T1")
    for n, raw_id in enumerate(["p1", "p1", "p4", "p4", "p4"]):
        raw = Item(
            id=raw_id,
            fill_ratio=0.55,
            position="arriba-izquierda",
            confidence=0.9,
        )
        smoothed = smoother.update([raw])
        state = update_table(
            state,
            obs(
                30 * n,
                *((f"T1-{i.id}", i.state, i.position) for i in smoothed),
            ),
        )
        assert set(state.plates) == {"T1-p1"}
    assert state.plates["T1-p1"].first_seen == START
