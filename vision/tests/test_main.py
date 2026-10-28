"""Tests del loop principal (main.py) con el clasificador falso."""

import json
from datetime import UTC, datetime, timedelta

import cv2
import numpy as np

import main as loop
from servio_vision import alerts, privacy, settings
from servio_vision.app import build_pipeline
from servio_vision.capture import IMAGE_SUFFIXES
from servio_vision.classifiers.fake import FakeClassifier
from servio_vision.cli import build_parser, settings_from_args
from servio_vision.pipeline import Pipeline
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


def run(pipeline: Pipeline, readings: int):
    out = []
    for n in range(readings):
        out.append(pipeline.process_frame(FRAME, T0 + timedelta(seconds=30 * n)))
    pipeline.close()
    return out


def test_one_observation_per_table_with_contract_shape():
    received = []
    pipeline = Pipeline(FakeClassifier(), ZONES, received.append)
    [observations] = run(pipeline, 1)
    assert [o.table_id for o in observations] == ["T1", "T2"]
    assert received == observations
    obs = observations[0]
    assert obs.schema_version == 1 and obs.ts == T0
    assert [p.track_id for p in obs.items] == ["T1-p1", "T1-p2"]


def test_observations_are_smoothed():
    script = [("lleno", 1), ("comiendo", 3)]
    pipeline = Pipeline(FakeClassifier(script=script), ZONES, lambda o: None)
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
    pipeline = Pipeline(FlakyClassifier(), ZONES, received.append)
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
    run(Pipeline(classifier, zones, lambda o: None), 2)
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
    monkeypatch.setattr(alerts, "DEFAULT_LOG", log)
    monkeypatch.setattr(alerts, "_processor", None)

    code = loop.main(
        ["--source", str(video), "--zones", str(zones_path), "--no-telegram"]
    )
    alerts._processor.close()

    assert code == 0
    events = [json.loads(line) for line in log.read_text("utf-8").splitlines()]
    lines = [e for e in events if e["event"] == "observation"]
    assert len(lines) == 2 * 4  # 2 mesas × lecturas a los 0, 30, 60 y 90 s
    assert lines[0]["table_id"] == "T1" and lines[0]["ts"].endswith("Z")


def _images_under(root) -> list:
    return sorted(p for p in root.rglob("*") if p.suffix.lower() in IMAGE_SUFFIXES)


class _OneFace:
    def detect(self, frame):
        return [(20, 20, 30, 30)]


def _noisy_video(tmp_path, seconds=65):
    """Video de un gradiente liso (el códec casi no lo toca) con un damero fino donde
    el detector falso ve la cara (20..50): pixelado queda gris, muy distinto."""
    frame = np.zeros((100, 200, 3), np.uint8)
    frame[:] = np.linspace(0, 255, 200, dtype=np.uint8)[None, :, None]
    checker = (np.indices((30, 30)).sum(axis=0) // 3 % 2 * 255).astype(np.uint8)
    frame[20:50, 20:50] = checker[:, :, None]
    video = tmp_path / "salon.avi"
    writer = cv2.VideoWriter(
        str(video), cv2.VideoWriter_fourcc(*"MJPG"), 10, (200, 100)
    )
    for _ in range(10 * seconds):
        writer.write(frame)
    writer.release()
    return video, frame


def _cli(tmp_path, monkeypatch, *extra) -> tuple[int, np.ndarray]:
    """Corre main.py sobre el video sintético; devuelve (código, frame original)."""
    zones_path = tmp_path / "zones.json"
    save_zones(ZONES, zones_path)
    monkeypatch.delenv("DEMO_MODE", raising=False)
    monkeypatch.setattr(alerts, "DEFAULT_LOG", tmp_path / "eventos.jsonl")
    monkeypatch.setattr(alerts, "_processor", None)
    monkeypatch.chdir(tmp_path)  # cualquier out/ relativo cae acá
    video, frame = _noisy_video(tmp_path)
    args = ["--source", str(video), "--zones", str(zones_path), "--no-telegram"]
    return loop.main([*args, *extra]), frame


def test_cli_saves_no_images_without_record(tmp_path, monkeypatch):
    """C-3: sin --record no queda ninguna imagen en disco."""
    code, _ = _cli(tmp_path, monkeypatch)
    alerts._processor.close()
    assert code == 0
    assert _images_under(tmp_path) == []


def test_cli_record_saves_one_blurred_frame_per_reading(tmp_path, monkeypatch):
    monkeypatch.setattr(privacy, "load_detector", lambda *a, **k: _OneFace())
    record = tmp_path / "grabacion"
    code, original = _cli(
        tmp_path, monkeypatch, "--record", str(record), "--limit", "3"
    )
    assert code == 0
    alerts._processor.close()
    saved = sorted(record.iterdir())
    assert [p.suffix for p in saved] == [".jpg"] * 3
    assert all(p.name.endswith("Z.jpg") for p in saved)
    assert _images_under(tmp_path) == saved  # y en ningún otro lado
    # la cara (20..50) está pixelada; el resto del frame es el original (JPEG mediante)
    image = cv2.imread(str(saved[0]))
    face_diff = np.abs(image[20:50, 20:50].astype(int) - original[20:50, 20:50]).mean()
    rest_diff = np.abs(image[70:100, 100:200].astype(int) - original[70:100, 100:200])
    assert face_diff > 3 * rest_diff.mean()


def test_cli_explains_when_the_face_model_is_missing(tmp_path, monkeypatch, caplog):
    def no_model(*args, **kwargs):
        raise privacy.PrivacyError("No se pudo bajar el detector de caras")

    monkeypatch.setattr(privacy, "load_detector", no_model)
    assert _cli(tmp_path, monkeypatch)[0] == 2
    assert "detector de caras" in caplog.text


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
    [observations] = run(Pipeline(FakeClassifier(), zones, lambda o: None), 1)
    assert [o.table_id for o in observations] == ["T1", "T2"]


def test_alerts_error_does_not_stop_the_loop():
    def broken_alerts(observation):
        raise RuntimeError("Telegram caído")

    [observations] = run(Pipeline(FakeClassifier(), ZONES, broken_alerts), 1)
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


def test_config_reaches_alerts(tmp_path, monkeypatch):
    """`build_pipeline` configura alerts con los umbrales resueltos, Telegram o
    consola según la configuración, y el token y el grupo que vienen con ella."""
    zones_path = tmp_path / "zones.json"
    save_zones(ZONES, zones_path)
    monkeypatch.delenv("DEMO_MODE", raising=False)
    calls = []
    monkeypatch.setattr(
        alerts,
        "configure",
        lambda config, telegram, restaurant=None, env=None: calls.append(
            (config.t1, telegram, env["TELEGRAM_GROUP_CHAT_ID"])
        ),
    )
    monkeypatch.setattr(alerts, "process", lambda o: [], raising=False)
    args = build_parser().parse_args(
        ["--source", "0", "--zones", str(zones_path), "--demo", "--no-telegram"]
    )
    cfg = settings_from_args(args).model_copy(update={"telegram_group_chat_id": "-42"})
    assert build_pipeline(cfg) is not None
    assert calls == [(60, False, "-42")]


def test_env_file_thresholds_apply(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("ALERT_T2=120\n", encoding="utf-8")
    monkeypatch.setattr(settings, "ENV_FILE", env)
    monkeypatch.delenv("ALERT_T2", raising=False)
    monkeypatch.delenv("DEMO_MODE", raising=False)
    zones_path = tmp_path / "zones.json"
    save_zones(ZONES, zones_path)
    args = build_parser().parse_args(
        ["--source", str(tmp_path), "--zones", str(zones_path), "--demo"]
    )
    configured = []
    monkeypatch.setattr(
        alerts,
        "configure",
        lambda config, telegram, restaurant=None, **kw: configured.append(config),
    )
    monkeypatch.setattr(alerts, "process", lambda o: [], raising=False)
    assert build_pipeline(settings_from_args(args)) is not None
    assert configured[0].t2 == 120  # del .env, no 2 × interval
    monkeypatch.delenv("ALERT_T2")
