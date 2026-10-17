"""Tests de la observación de mesa, del aviso y de los fixtures (03-contratos.md)."""

import itertools
import json
from collections import defaultdict
from datetime import UTC, timedelta
from pathlib import Path
from typing import Literal

import pytest
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from servio_vision.alerts.models import Alert, AlertType, TableObservation

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
OBSERVATIONS = FIXTURES / "observations"
EXPECTED = FIXTURES / "expected"
INTERVAL = 30  # segundos entre lecturas, como el pipeline (04-vision.md)
SCENARIOS = ["mesa_completa", "plato_lleno_mucho", "id_cambiado"]


class ExpectedEvent(BaseModel):
    """Formato de fixtures/expected/*.alerts.json."""

    model_config = ConfigDict(strict=True, extra="forbid")

    at_seconds: int = Field(ge=0)
    event: Literal["alert_opened", "alert_reminder", "alert_resolved"]
    type: AlertType
    table_id: str


def load(name: str) -> list[TableObservation]:
    lines = (OBSERVATIONS / f"{name}.jsonl").read_text(encoding="utf-8").splitlines()
    return [
        TableObservation.model_validate_json(line) for line in lines if line.strip()
    ]


PLATE = {
    "track_id": "T1-p1",
    "state": "lleno",
    "fill_ratio": 0.9,
    "confidence": 0.9,
    "position": "centro",
}


def observation(**overrides):
    data = {
        "schema_version": 1,
        "camera_id": "cam-1",
        "ts": "2026-10-17T20:00:00Z",
        "table_id": "T1",
        "people_present": True,
        "items": [dict(PLATE)],
    }
    return data | overrides


def with_plate(**plate):
    # Sin fill_ratio por defecto, para poder probar estados sueltos.
    base = {k: v for k, v in PLATE.items() if k != "fill_ratio"}
    return observation(items=[base | plate])


def without(key: str):
    plate = dict(PLATE)
    del plate[key]
    return observation(items=[plate])


# --- fixtures ---


@pytest.mark.parametrize("name", SCENARIOS)
def test_scenario_has_observations_and_expected(name: str):
    assert (OBSERVATIONS / f"{name}.jsonl").exists()
    assert (EXPECTED / f"{name}.alerts.json").exists()


@pytest.mark.parametrize(
    "path", sorted(OBSERVATIONS.glob("*.jsonl")), ids=lambda p: p.name
)
def test_observations_match_contract(path: Path):
    # Por mesa: una lectura cada INTERVAL. Puede haber varias mesas en el mismo ts.
    observations = load(path.stem)
    assert observations
    by_table = defaultdict(list)
    for obs in observations:
        by_table[obs.table_id].append(obs.ts)
        for item in obs.items:
            assert item.track_id.startswith(f"{obs.table_id}-")
    for table_id, stamps in by_table.items():
        steps = [b - a for a, b in itertools.pairwise(stamps)]
        assert all(s == timedelta(seconds=INTERVAL) for s in steps), table_id


@pytest.mark.parametrize(
    "path", sorted(EXPECTED.glob("*.alerts.json")), ids=lambda p: p.name
)
def test_expected_matches_its_observations(path: Path):
    events = [
        ExpectedEvent.model_validate(e) for e in json.loads(path.read_text("utf-8"))
    ]
    assert [e.at_seconds for e in events] == sorted(e.at_seconds for e in events)
    observations = load(path.name.removesuffix(".alerts.json"))
    start = observations[0].ts  # at_seconds cuenta desde la primera línea
    ticks = {int((obs.ts - start).total_seconds()) for obs in observations}
    tables = {obs.table_id for obs in observations}
    for e in events:
        assert e.at_seconds in ticks, (
            f"{e.at_seconds} s no es una lectura del escenario"
        )
        assert e.table_id in tables, f"{e.table_id} no está en el escenario"


def test_mesa_completa_goes_from_full_to_empty_in_order():
    states = [
        it.state
        for obs in load("mesa_completa")
        for it in obs.items
        if it.track_id == "T1-p1"
    ]
    transitions = [s for i, s in enumerate(states) if i == 0 or s != states[i - 1]]
    assert transitions == ["lleno", "comiendo", "por_terminar", "vacio"]


def test_plato_lleno_mucho_stays_full_with_people_12_min_in_a_row():
    longest = 0.0
    run_start = None
    for obs in load("plato_lleno_mucho"):
        full = any(it.track_id == "T2-p1" and it.state == "lleno" for it in obs.items)
        if full and obs.people_present:
            run_start = run_start or obs.ts
            longest = max(longest, (obs.ts - run_start).total_seconds())
        else:
            run_start = None
    assert longest >= 12 * 60


def test_id_cambiado_changes_one_id_in_same_position():
    # Como el suavizado real: el id nuevo llega junto al viejo (retenido una lectura)
    # en la misma posición, y en la lectura siguiente el viejo ya no viene.
    observations = load("id_cambiado")
    swaps = []
    for i, (prev, curr) in enumerate(itertools.pairwise(observations), start=1):
        prev_ids = {it.track_id for it in prev.items}
        for new in (it for it in curr.items if it.track_id not in prev_ids):
            olds = [p.track_id for p in prev.items if p.position == new.position]
            swaps += [(i, old) for old in olds]
    assert len(swaps) == 1
    i, old = swaps[0]
    assert old in {it.track_id for it in observations[i].items}  # viene junto al nuevo
    assert old not in {it.track_id for it in observations[i + 1].items}  # y después no


# --- modelos ---


def test_people_present_missing_or_null_means_true():
    data = observation()
    del data["people_present"]
    assert TableObservation.model_validate(data).people_present is True
    assert (
        TableObservation.model_validate(observation(people_present=None)).people_present
        is True
    )


def test_json_round_trip():
    obs = TableObservation.model_validate(observation(ts="2026-10-17T17:00:00-03:00"))
    assert TableObservation.model_validate_json(obs.model_dump_json()) == obs


def test_ts_is_stored_in_utc():
    obs = TableObservation.model_validate(observation(ts="2026-10-17T17:00:00-03:00"))
    assert obs.ts.tzinfo == UTC
    assert obs.ts.hour == 20


def test_observation_is_read_only():
    obs = TableObservation.model_validate(observation())
    with pytest.raises(ValidationError):
        obs.table_id = "T9"
    with pytest.raises(ValidationError):
        obs.items[0].state = "vacio"
    assert obs.table_id == "T1"
    assert obs.items[0].state == "lleno"


ALERT = {
    "id": "a1",
    "table_id": "T1",
    "type": "plato_vacio",
    "message": "🧹 Mesa 1: hay platos vacíos para retirar.",
    "created_at": "2026-10-17T20:04:00Z",
    "status": "abierta",
}


def test_alert_defaults():
    alert = Alert.model_validate(ALERT)
    assert alert.reminders_sent == 0
    assert alert.telegram_message_id is None


def test_alert_rejects_more_than_two_reminders():
    Alert.model_validate(ALERT | {"reminders_sent": 2})
    with pytest.raises(ValidationError):
        Alert.model_validate(ALERT | {"reminders_sent": 3})


def test_alert_validates_assignment():
    alert = Alert.model_validate(ALERT)
    alert.reminders_sent = 2
    with pytest.raises(ValidationError):
        alert.reminders_sent = 3
    with pytest.raises(ValidationError):
        alert.status = "cerrada"
    # Un error no deja el valor malo puesto.
    assert alert.reminders_sent == 2
    assert alert.status == "abierta"


@pytest.mark.parametrize(
    "data",
    [
        pytest.param(observation(schema_version=2), id="schema_version_2"),
        pytest.param(observation(schema_version=True), id="schema_version_true"),
        pytest.param(observation(schema_version=1.0), id="schema_version_float"),
        pytest.param(observation(ts="2026-10-17T20:00:00"), id="ts_without_zone"),
        pytest.param(observation(table="T1"), id="unknown_field"),
        pytest.param(with_plate(fillRatio=0.2), id="unknown_plate_field"),
        pytest.param(with_plate(fill_ratio=0.1), id="fill_ratio_contradicts_state"),
        pytest.param(with_plate(confidence="0.8"), id="confidence_string"),
        pytest.param(with_plate(state="llena"), id="unknown_state"),
        pytest.param(
            observation(items=observation()["items"] * 2), id="duplicate_track_id"
        ),
        pytest.param(with_plate(track_id="T2-p1"), id="track_id_from_other_table"),
        pytest.param(with_plate(track_id="T1-"), id="track_id_without_plate_id"),
        pytest.param(observation(ts="2026-10-17 20:00:00Z"), id="ts_with_space"),
        pytest.param(observation(ts="20261017T200000Z"), id="ts_compact"),
        pytest.param(observation(ts=1792267200), id="ts_unix"),
        pytest.param(observation(ts="2026-10-17T20:00:00Z\n"), id="ts_newline"),
        pytest.param(
            observation(ts="2026-10-17T20:00:00.1234567Z"), id="ts_7_decimals"
        ),
        pytest.param(without("position"), id="missing_position"),
        pytest.param(without("confidence"), id="missing_confidence"),
    ],
)
def test_rejects(data):
    with pytest.raises(ValidationError):
        TableObservation.model_validate(data)


@pytest.mark.parametrize(
    ("fill_ratio", "state"), [(0.80, "comiendo"), (0.05, "por_terminar")]
)
def test_accepts_fill_ratio_on_threshold(fill_ratio, state):
    TableObservation.model_validate(with_plate(fill_ratio=fill_ratio, state=state))
