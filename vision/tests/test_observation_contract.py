from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from servio_vision.contracts import Observation
from servio_vision.schemas import state_from_fill_ratio

OBSERVATIONS = Path(__file__).resolve().parents[2] / "fixtures" / "observations"
START = datetime(2026, 10, 17, 20, 0, 0, tzinfo=UTC)


def observation(ts="2026-10-17T20:00:00Z", **item):
    base = {"track_id": "T1-p1", "kind": "plate", "state": "lleno", "confidence": 0.9}
    return {
        "schema_version": 1,
        "camera_id": "cam-1",
        "ts": ts,
        "tables": [{"table_id": "T1", "items": [{**base, **item}]}],
    }


@pytest.mark.parametrize(
    "path", sorted(OBSERVATIONS.glob("*.jsonl")), ids=lambda p: p.name
)
def test_observaciones_cumplen_contrato(path: Path):
    lines = path.read_text(encoding="utf-8").splitlines()
    observations = [
        Observation.model_validate_json(line) for line in lines if line.strip()
    ]
    assert observations
    for i, obs in enumerate(observations):
        assert obs.ts == START + timedelta(seconds=10 * i)


def test_estan_los_escenarios_de_t0_2():
    names = {p.name for p in OBSERVATIONS.glob("*.jsonl")}
    assert {
        "mesa_completa.jsonl",
        "plato_lleno_mucho.jsonl",
        "id_cambiado.jsonl",
    } <= names


def test_acepta_null_en_opcionales_y_ts_con_offset():
    data = observation(ts="2026-10-17T17:00:00-03:00", fill_ratio=None, position=None)
    data["tables"][0]["people_present"] = None
    Observation.model_validate(data)


def test_ida_y_vuelta_por_json():
    # Lo que serializa la visión tiene que volver a validar (y en web/ lo valida Zod).
    obs = Observation.model_validate(observation())
    assert Observation.model_validate_json(obs.model_dump_json()) == obs


@pytest.mark.parametrize(
    "data",
    [
        pytest.param({**observation(), "schema_version": 2}, id="schema_version"),
        pytest.param(observation(kind="drink"), id="kind_fuera_de_alcance"),
        pytest.param(observation(ts="2026-10-17T20:00:00"), id="ts_sin_zona"),
        pytest.param(observation(confidence="0.8"), id="confidence_string"),
    ],
)
def test_rechaza(data):
    with pytest.raises(ValidationError):
        Observation.model_validate(data)


@pytest.mark.parametrize(
    "path", sorted(OBSERVATIONS.glob("*.jsonl")), ids=lambda p: p.name
)
def test_fill_ratio_coherente_con_estado(path: Path):
    for line in path.read_text(encoding="utf-8").splitlines():
        for table in Observation.model_validate_json(line).tables:
            for item in table.items:
                if item.fill_ratio is not None:
                    assert state_from_fill_ratio(item.fill_ratio) == item.state, (
                        item.track_id
                    )
