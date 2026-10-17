from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from servio_vision.contracts import Observation

OBSERVATIONS = Path(__file__).resolve().parents[2] / "fixtures" / "observations"
START = datetime(2026, 10, 17, 20, 0, 0, tzinfo=UTC)


@pytest.mark.parametrize("path", sorted(OBSERVATIONS.glob("*.jsonl")), ids=lambda p: p.name)
def test_observaciones_cumplen_contrato(path: Path):
    lines = path.read_text(encoding="utf-8").splitlines()
    observations = [Observation.model_validate_json(line) for line in lines if line.strip()]
    assert observations
    for i, obs in enumerate(observations):
        assert obs.ts == START + timedelta(seconds=10 * i)


def test_hay_fixtures():
    assert len(list(OBSERVATIONS.glob("*.jsonl"))) == 3


def test_rechaza_schema_version_desconocida():
    with pytest.raises(ValidationError):
        Observation.model_validate(
            {"schema_version": 2, "camera_id": "cam-1", "ts": "2026-10-17T20:00:00Z", "tables": []}
        )


def test_rechaza_kind_fuera_de_alcance():
    item = {"track_id": "T1-d1", "kind": "drink", "state": "vacio", "confidence": 0.9}
    with pytest.raises(ValidationError):
        Observation.model_validate(
            {
                "schema_version": 1,
                "camera_id": "cam-1",
                "ts": "2026-10-17T20:00:00Z",
                "tables": [{"table_id": "T1", "items": [item]}],
            }
        )
