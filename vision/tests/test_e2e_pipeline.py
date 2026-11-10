"""Punta a punta (A-8): `main.py` entero con `--classifier fake --fake-mode replay`
sobre cada escenario de `fixtures/observations/`, y los avisos del log contra los
esperados. Pasa por todo lo que no es el modelo: fuente (una carpeta de fotos
sintéticas), recorte de la mesa, suavizado, alerts y el log.

Si cambian los avisos, el CI falla. Si el cambio es a propósito, regenerar los
esperados de `fixtures/pipeline/` y revisar el diff en el PR:

    SERVIO_UPDATE_GOLDEN=1 pytest tests/test_e2e_pipeline.py
"""

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import eval as evaluation

from tests.test_alert_engine import FIXTURES, SCENARIOS

GOLDEN = FIXTURES / "pipeline"
UPDATE = os.getenv("SERVIO_UPDATE_GOLDEN", "").strip() == "1"
# Sesiones grabadas (B-1): cuando haya una en fixtures/, sumar acá su nombre (y su
# esperado en fixtures/pipeline/). Si viene con el formato del log (`event` en cada
# línea), filtrar las observaciones antes de pasársela al fake.
SESSIONS: list[str] = []


@pytest.mark.parametrize("name", SCENARIOS + SESSIONS)
def test_pipeline_alerts_match_golden(name, tmp_path):
    """Lo mismo que corre `tools/eval.py --all` para «escenarios que pasan»: el
    escenario por `main.py` entero con el fake, en una carpeta temporal con sus
    propios log y base, comparado con `fixtures/pipeline/`."""
    result = evaluation.run_scenario(name, tmp_path)
    if UPDATE:
        golden = GOLDEN / f"{name}.alerts.json"
        golden.parent.mkdir(exist_ok=True)
        golden.write_text(
            "[\n"
            + ",\n".join("  " + json.dumps(e) for e in result["avisos"])
            + "\n]\n",
            "utf-8",
        )
        pytest.fail(f"{golden.name} regenerado: revisar el diff")
    assert result["pasa"], result["diferencias"]
