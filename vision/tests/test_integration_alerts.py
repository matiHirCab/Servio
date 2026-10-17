"""Integración del loop con las reglas (MAT-7): clasificador falso → main.Pipeline →
alerts.process() con umbrales de demo, sin Telegram. Es la versión automática de
`python main.py --source <video> --classifier fake --demo`."""

import json
import logging
from datetime import UTC, datetime, timedelta

import numpy as np

import main as loop
from servio_vision import alerts
from servio_vision.alerts.config import load_config
from servio_vision.classifiers.fake import FakeClassifier
from servio_vision.zones import TableZone, Zones

ZONES = Zones(
    frame_size=(200, 100),
    tables=[
        TableZone(table_id="T1", name="Mesa 1", polygon=[(0, 0), (90, 0), (90, 90)]),
        TableZone(
            table_id="T2", name="Mesa 2", polygon=[(110, 0), (199, 0), (199, 90)]
        ),
    ],
)
FRAME = np.zeros((100, 200, 3), np.uint8)
T0 = datetime(2026, 10, 17, 20, 0, tzinfo=UTC)
INTERVAL = 30
READINGS = 20  # 10 min de video: cada mesa abre y cierra los tres avisos
ALERT_TYPES = {"mesa_por_terminar", "plato_vacio", "plato_lleno_mucho"}


def test_fake_loop_with_demo_thresholds_opens_and_closes_the_three_alerts(
    tmp_path, monkeypatch, caplog
):
    monkeypatch.setattr(alerts, "_processor", None)
    log = tmp_path / "eventos.jsonl"
    alerts.configure(
        load_config(INTERVAL, demo=True, env={}), telegram=False, log_path=log
    )
    pipeline = loop.Pipeline(FakeClassifier(), ZONES, alerts.process)
    try:
        with caplog.at_level(logging.ERROR):
            for n in range(READINGS):
                pipeline.process_frame(FRAME, T0 + timedelta(seconds=INTERVAL * n))
    finally:
        # cerrar siempre: en Windows un log abierto bloquea tmp_path
        pipeline.close()
        alerts._processor.close()

    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
    events = [json.loads(line) for line in log.read_text("utf-8").splitlines()]
    for table_id in ("T1", "T2"):
        opened = {
            e["id"]: e["type"]
            for e in events
            if e["event"] == "alert_opened" and e["table_id"] == table_id
        }
        resolved = {
            e["id"]
            for e in events
            if e["event"] == "alert_resolved" and e["table_id"] == table_id
        }
        assert set(opened.values()) == ALERT_TYPES, table_id
        # cada tipo se cierra solo cuando la condición desaparece (al cortar puede
        # quedar abierto el de la comida siguiente)
        assert {opened[i] for i in resolved} == ALERT_TYPES, table_id
