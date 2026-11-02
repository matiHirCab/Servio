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
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
import pytest

import main as loop
from servio_vision.zones import TableZone, Zones, save_zones
from tests.test_alert_engine import FIXTURES, SCENARIOS

GOLDEN = FIXTURES / "pipeline"
UPDATE = os.getenv("SERVIO_UPDATE_GOLDEN", "").strip() == "1"
# Sesiones grabadas (B-1): cuando haya una en fixtures/, sumar acá su nombre (y su
# esperado en fixtures/pipeline/). Si viene con el formato del log (`event` en cada
# línea), filtrar las observaciones antes de pasársela al fake.
SESSIONS: list[str] = []
SIZE = (200, 100)  # ancho × alto de las fotos sintéticas


def write_frames(folder: Path, count: int) -> None:
    """Una foto negra por lectura: el fake no mira la imagen."""
    folder.mkdir()
    frame = np.zeros((SIZE[1], SIZE[0], 3), np.uint8)
    for n in range(count):
        cv2.imwrite(str(folder / f"{n:04d}.png"), frame)


def alert_events(log: Path) -> list[dict]:
    """Los avisos del log con el formato de `fixtures/expected/`: segundos desde la
    primera observación."""
    events = [json.loads(line) for line in log.read_text("utf-8").splitlines()]
    start = datetime.fromisoformat(
        next(e["ts"] for e in events if e["event"] == "observation")
    )
    return [
        {
            "at_seconds": int(
                (datetime.fromisoformat(e["ts"]) - start).total_seconds()
            ),
            "event": e["event"],
            "type": e["type"],
            "table_id": e["table_id"],
        }
        for e in events
        if e["event"].startswith("alert_")
    ]


@pytest.mark.parametrize("name", SCENARIOS + SESSIONS)
def test_pipeline_alerts_match_golden(name, tmp_path, monkeypatch):
    fixture = FIXTURES / "observations" / f"{name}.jsonl"
    lines = [json.loads(line) for line in fixture.read_text("utf-8").splitlines()]
    table_ids = sorted({line["table_id"] for line in lines})
    readings = len(lines) // len(table_ids)
    frames = tmp_path / "frames"
    write_frames(frames, readings)
    # las mesas, una al lado de la otra, cubriendo la foto
    width = SIZE[0] // len(table_ids)
    zones = Zones(
        frame_size=SIZE,
        tables=[
            TableZone(
                table_id=table_id,
                name=table_id,
                polygon=[
                    (i * width, 0),
                    ((i + 1) * width - 1, 0),
                    ((i + 1) * width - 1, SIZE[1] - 1),
                    (i * width, SIZE[1] - 1),
                ],
            )
            for i, table_id in enumerate(table_ids)
        ],
    )
    zones_path = tmp_path / "zones.json"
    save_zones(zones, zones_path)
    # umbrales de producción y base SQLite junto al log de tmp_path (conftest)
    for var in ("DEMO_MODE", "DATABASE_URL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path)

    code = loop.main(
        [
            "--source", str(frames),
            "--zones", str(zones_path),
            "--classifier", "fake",
            "--fake-mode", "replay",
            "--fixture", str(fixture),
            "--interval", "30",
            "--no-telegram",
        ]
    )  # fmt: skip

    assert code == 0
    got = alert_events(tmp_path / "eventos.jsonl")
    golden = GOLDEN / f"{name}.alerts.json"
    if UPDATE:
        golden.parent.mkdir(exist_ok=True)
        golden.write_text(
            "[\n" + ",\n".join("  " + json.dumps(e) for e in got) + "\n]\n", "utf-8"
        )
        pytest.fail(f"{golden.name} regenerado: revisar el diff")
    assert got == json.loads(golden.read_text("utf-8"))
