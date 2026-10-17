"""Tests del loop principal (main.py) con el clasificador falso."""

import json
from datetime import UTC, datetime, timedelta

import cv2
import numpy as np

import main as loop
from servio_vision.classifiers.fake import FakeClassifier
from servio_vision.schemas import TableReport
from servio_vision.zones import TableZone, Zones, save_zones

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


def run(pipeline: loop.Pipeline, readings: int):
    out = []
    for n in range(readings):
        out.append(pipeline.process_frame(FRAME, T0 + timedelta(seconds=30 * n)))
    pipeline.close()
    return out


def test_one_observation_per_table_with_contract_shape():
    received = []
    pipeline = loop.Pipeline(FakeClassifier(), ZONES, received.append)
    [observations] = run(pipeline, 1)
    assert [o.table_id for o in observations] == ["T1", "T2"]
    assert received == observations
    obs = observations[0]
    assert obs.schema_version == 1 and obs.ts == T0
    assert [p.track_id for p in obs.items] == ["T1-p1", "T1-p2"]


def test_observations_are_smoothed():
    script = [("lleno", 1), ("comiendo", 3)]
    pipeline = loop.Pipeline(FakeClassifier(script=script), ZONES, lambda o: None)
    states = [r[0].items[0].state for r in run(pipeline, 3)]
    # el falso dice "comiendo" desde la 2.ª lectura; el suavizado lo acepta en la 3.ª
    assert states == ["lleno", "lleno", "comiendo"]


class FlakyClassifier:
    """T1 falla siempre; T2 tira una excepción."""

    def classify(self, image, table_id, previous_items):
        if table_id == "T1":
            return
        raise RuntimeError("se cayó")


def test_failed_readings_send_nothing():
    received = []
    pipeline = loop.Pipeline(FlakyClassifier(), ZONES, received.append)
    assert run(pipeline, 1) == [[]]
    assert received == []


class RecordingClassifier:
    def __init__(self):
        self.previous = []

    def classify(self, image, table_id, previous_items):
        self.previous.append([i.id for i in previous_items])
        assert (
            cv2.imdecode(np.frombuffer(image, np.uint8), cv2.IMREAD_COLOR) is not None
        )
        return TableReport.model_validate(
            {
                "items": [
                    {"id": "p1", "state": "lleno", "position": "c", "confidence": 1}
                ]
            }
        )


def test_previous_items_are_the_smoothed_plates():
    classifier = RecordingClassifier()
    zones = Zones(frame_size=(200, 100), tables=ZONES.tables[:1])
    run(loop.Pipeline(classifier, zones, lambda o: None), 2)
    assert classifier.previous == [[], ["p1"]]


def test_cli_logs_observations(tmp_path, monkeypatch):
    video = tmp_path / "salon.avi"
    writer = cv2.VideoWriter(
        str(video), cv2.VideoWriter_fourcc(*"MJPG"), 10, (200, 100)
    )
    for _ in range(10 * 95):
        writer.write(FRAME)
    writer.release()
    zones_path = tmp_path / "zones.json"
    save_zones(ZONES, zones_path)
    monkeypatch.delenv("DEMO_MODE", raising=False)
    log = tmp_path / "eventos.jsonl"
    monkeypatch.setattr(loop.alerts, "DEFAULT_LOG", log)
    monkeypatch.setattr(loop.alerts, "_processor", None)

    code = loop.main(
        ["--source", str(video), "--zones", str(zones_path), "--no-telegram"]
    )
    loop.alerts._processor.close()

    assert code == 0
    events = [json.loads(line) for line in log.read_text("utf-8").splitlines()]
    lines = [e for e in events if e["event"] == "observation"]
    assert len(lines) == 2 * 4  # 2 mesas × lecturas a los 0, 30, 60 y 90 s
    assert lines[0]["table_id"] == "T1" and lines[0]["ts"].endswith("Z")


def test_cli_without_zones_explains_what_to_do(tmp_path, caplog):
    code = loop.main(["--source", str(tmp_path), "--zones", str(tmp_path / "no.json")])
    assert code == 2
    assert "draw_zones" in caplog.text


def test_one_bad_table_does_not_stop_the_others():
    zones = Zones(
        frame_size=(200, 100),
        tables=[
            *ZONES.tables,
            # fuera del frame: no se puede recortar
            TableZone(
                table_id="T3",
                name="Mesa 3",
                polygon=[(500, 500), (600, 500), (600, 600)],
            ),
        ],
    )
    [observations] = run(loop.Pipeline(FakeClassifier(), zones, lambda o: None), 1)
    assert [o.table_id for o in observations] == ["T1", "T2"]


def test_alerts_error_does_not_stop_the_loop():
    def broken_alerts(observation):
        raise RuntimeError("Telegram caído")

    [observations] = run(loop.Pipeline(FakeClassifier(), ZONES, broken_alerts), 1)
    assert len(observations) == 2


def test_invalid_interval_explains_and_exits(tmp_path, caplog, monkeypatch):
    zones_path = tmp_path / "zones.json"
    save_zones(ZONES, zones_path)
    monkeypatch.delenv("DEMO_MODE", raising=False)
    # producción: T1 = 60 s < 2 × 60
    code = loop.main(
        ["--source", str(tmp_path), "--zones", str(zones_path), "--interval", "60"]
    )
    assert code == 2
    assert "Umbrales de aviso inválidos" in caplog.text


def test_config_reaches_alerts(monkeypatch):
    calls = []
    monkeypatch.setattr(loop.alerts, "process", calls.append, raising=False)
    monkeypatch.setattr(
        loop.alerts,
        "configure",
        lambda config, telegram: calls.append((config.t1, telegram)),
        raising=False,
    )
    config = loop.load_config(30, demo=True, env={})
    handler = loop.observation_handler(config, no_telegram=True)
    assert calls == [(60, False)]
    assert handler == calls.append
