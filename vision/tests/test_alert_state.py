"""Tests del estado por plato, el cambio de id y table_status (MAT-2)."""

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


def at(seconds: int) -> datetime:
    return START + timedelta(seconds=seconds)


def load(name: str) -> list[TableObservation]:
    lines = (OBSERVATIONS / f"{name}.jsonl").read_text(encoding="utf-8").splitlines()
    return [
        TableObservation.model_validate_json(line) for line in lines if line.strip()
    ]


def replay(observations: list[TableObservation]) -> dict[int, TableState]:
    """Estado después de cada observación, por segundo desde START."""
    state = empty_table(observations[0].table_id)
    states = {}
    for o in observations:
        state = update_table(state, o)
        states[int((o.ts - START).total_seconds())] = state
    return states


def obs(seconds: int, *plates: tuple[str, str, str]) -> TableObservation:
    """Observación de T1 a START + seconds con platos (track_id, state, position)."""
    return TableObservation(
        schema_version=1,
        camera_id="cam-1",
        ts=at(seconds),
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


def active_ids(state: TableState) -> set[str]:
    return {p.track_id for p in state.active_plates()}


# --- fixtures ---


def test_id_cambiado_new_id_is_provisional_then_inherits():
    states = replay(load("id_cambiado"))
    # 180 s: llegan T1-p1 (retenido por el suavizado) y T1-p4 juntos.
    assert states[180].plates["T1-p4"].provisional
    assert active_ids(states[180]) == {"T1-p1", "T1-p2"}
    # 210 s: T1-p1 ya no viene y T1-p4 hereda sus tiempos.
    plate = states[210].plates["T1-p4"]
    assert "T1-p1" not in states[210].plates
    assert not plate.provisional
    assert plate.inherited_from == "T1-p1"
    assert plate.first_seen == plate.state_since == START
    assert active_ids(states[210]) == {"T1-p4", "T1-p2"}


def test_id_cambiado_keeps_one_plate_per_position_for_the_rules():
    for state in replay(load("id_cambiado")).values():
        positions = [p.position for p in state.active_plates()]
        assert len(positions) == len(set(positions))


def test_id_cambiado_removes_plate_when_it_stops_coming():
    states = replay(load("id_cambiado"))
    assert "T1-p2" in states[270].plates
    assert "T1-p2" not in states[300].plates
    assert set(states[330].plates) == {"T1-p4"}
    assert states[330].plates["T1-p4"].first_seen == START


def test_mesa_completa_removes_plates_and_starts_new_one():
    states = replay(load("mesa_completa"))
    assert set(states[480].plates) == {"T1-p1", "T1-p2"}
    assert states[510].plates == {}
    new = states[720].plates["T1-p3"]
    assert new.first_seen == new.state_since == at(720)
    assert new.inherited_from is None


def test_mesa_completa_state_since_changes_only_with_state():
    states = replay(load("mesa_completa"))
    plate = {second: s.plates.get("T1-p1") for second, s in states.items()}
    assert plate[150].state_since == at(90)  # lleno desde 90 s
    assert plate[240].state_since == at(180)  # comiendo desde 180 s
    assert plate[480].state_since == at(360)  # vacio desde 360 s
    assert plate[480].first_seen == at(90)


def test_mesa_completa_table_status_sequence():
    statuses = [
        table_status(p.state for p in s.active_plates())
        for s in replay(load("mesa_completa")).values()
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


def test_id_swap_through_real_smoother():
    # Integración con smoothing.py (JES-5): el clasificador cambia p1 → p4 en la
    # misma posición y las reglas tienen que ver un solo plato con su tiempo original.
    from servio_vision.schemas import Item
    from servio_vision.smoothing import TableSmoother

    smoother = TableSmoother()
    state = empty_table("T1")
    for n, raw_id in enumerate(["p1", "p1", "p4", "p4", "p4"]):
        raw = Item(
            id=raw_id, fill_ratio=0.55, position="arriba-izquierda", confidence=0.9
        )
        smoothed = smoother.update([raw])
        plates = ((f"T1-{i.id}", i.state, i.position) for i in smoothed)
        state = update_table(state, obs(30 * n, *plates))
        assert len(state.active_plates()) == 1
    (plate,) = state.active_plates()
    assert plate.track_id == "T1-p4"
    assert plate.first_seen == START


# --- cambio de id ---


def test_inherits_when_old_and_new_swap_in_same_reading():
    state = run(
        obs(0, ("T1-p1", "lleno", "centro")),
        obs(30, ("T1-p7", "lleno", "centro")),
    )
    plate = state.plates["T1-p7"]
    assert set(state.plates) == {"T1-p7"}
    assert plate.first_seen == plate.state_since == START
    assert plate.inherited_from == "T1-p1"
    assert not plate.provisional


def test_inherits_first_seen_but_not_state_since_when_state_differs():
    state = run(
        obs(0, ("T1-p1", "lleno", "centro")),
        obs(30, ("T1-p7", "comiendo", "centro")),
    )
    plate = state.plates["T1-p7"]
    assert plate.first_seen == START
    assert plate.state_since == at(30)


def test_second_plate_in_same_position_is_not_merged():
    # El review del #14: un segundo plato servido en el mismo casillero no se mezcla.
    first = obs(0, ("T1-p1", "lleno", "arriba-izquierda"))
    both = (
        ("T1-p1", "vacio", "arriba-izquierda"),
        ("T1-p2", "lleno", "arriba-izquierda"),
    )
    state = run(first, obs(30, *both))
    assert active_ids(state) == {"T1-p1"}  # T1-p2 provisional en su primera lectura
    state = update_table(state, obs(60, *both))
    assert active_ids(state) == {"T1-p1", "T1-p2"}
    assert state.plates["T1-p1"].state == "vacio"  # sus lecturas no se pierden
    assert state.plates["T1-p2"].first_seen == at(30)
    assert state.plates["T1-p2"].inherited_from is None


def test_old_second_plate_does_not_inherit_later():
    # Un plato que ya era propio (apareció hace más de una lectura) no hereda.
    both = ("T1-p1", "lleno", "centro"), ("T1-p2", "lleno", "centro")
    state = run(
        obs(0, both[0]),
        obs(30, *both),
        obs(60, *both),
        obs(90, both[1]),
    )
    assert state.plates["T1-p2"].first_seen == at(30)
    assert state.plates["T1-p2"].inherited_from is None


def test_no_inheritance_with_two_candidates():
    state = run(
        obs(0, ("T1-p1", "lleno", "centro")),
        obs(30, ("T1-p7", "lleno", "centro"), ("T1-p8", "lleno", "centro")),
    )
    assert all(p.first_seen == at(30) for p in state.plates.values())


def test_no_inheritance_with_two_removed_in_same_position():
    state = run(
        obs(0, ("T1-p1", "lleno", "centro"), ("T1-p2", "lleno", "centro")),
        obs(30, ("T1-p7", "lleno", "centro")),
    )
    assert state.plates["T1-p7"].first_seen == at(30)
    assert state.plates["T1-p7"].inherited_from is None


def test_moved_plate_and_new_plate_in_its_old_position_stay_apart():
    state = run(
        obs(0, ("T1-p1", "lleno", "centro")),
        obs(30, ("T1-p1", "lleno", "izquierda"), ("T1-p2", "comiendo", "centro")),
    )
    assert active_ids(state) == {"T1-p1", "T1-p2"}
    assert state.plates["T1-p1"].first_seen == START
    assert state.plates["T1-p2"].first_seen == at(30)


def test_new_plate_in_other_position_is_counted_right_away():
    state = run(
        obs(0, ("T1-p1", "lleno", "centro")),
        obs(30, ("T1-p1", "lleno", "centro"), ("T1-p2", "lleno", "izquierda")),
    )
    assert active_ids(state) == {"T1-p1", "T1-p2"}


def test_removed_plate_without_candidate_just_leaves():
    state = run(obs(0, ("T1-p1", "lleno", "centro")), obs(30))
    assert state.plates == {}


# --- pureza y validación ---


def test_update_does_not_modify_previous_state():
    before = run(obs(0, ("T1-p1", "lleno", "centro")))
    snapshot = (dict(before.plates), before.last_ts)
    update_table(before, obs(30, ("T1-p7", "comiendo", "centro")))
    assert (dict(before.plates), before.last_ts) == snapshot


def test_same_observation_twice_is_idempotent():
    swap = obs(30, ("T1-p1", "lleno", "centro"), ("T1-p4", "lleno", "centro"))
    once = run(obs(0, ("T1-p1", "lleno", "centro")), swap)
    assert update_table(once, swap) == once


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
