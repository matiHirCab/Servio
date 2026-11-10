"""Salón en vivo (F-1): la foto que arma `salon.py`. El servidor está en
`test_salon_web.py`."""

import json
import threading
from datetime import timedelta

from servio_vision.classifiers.fake import FakeClassifier
from servio_vision.pipeline import Pipeline
from servio_vision.salon import SalonFeed, plate_box, table_box
from tests.test_main import FRAME, T0, ZONES


def read(pipeline: Pipeline, readings: int) -> None:
    for n in range(readings):
        pipeline.process_frame(FRAME, T0 + timedelta(seconds=30 * n))


def test_table_and_plate_boxes_are_normalized_in_the_camera_image():
    box = table_box([(0, 0), (100, 0), (100, 50)], (200, 100))
    assert box == (0.0, 0.0, 0.5, 0.5)
    assert plate_box((0.5, 0.5, 0.2, 0.2), box) == (0.25, 0.25, 0.1, 0.1)
    assert plate_box(None, box) is None


def test_feed_publishes_every_table_with_its_plates_after_each_reading():
    feed = SalonFeed("casa-1", interval=30)
    pipeline = Pipeline(
        FakeClassifier(), ZONES, lambda o: None, on_reading=feed.on_reading
    )
    assert feed.snapshot() is None
    read(pipeline, 2)
    snap = feed.snapshot()
    assert snap is not None
    assert snap["version"] == 2
    assert snap["local"] == "casa-1"
    assert snap["frame"] == [200, 100]
    assert [t["name"] for t in snap["tables"]] == ["Mesa 1", "Mesa 2"]
    plates = [p for t in snap["tables"] for p in t["plates"]]
    assert plates, "el fake sirve platos desde la primera lectura"
    for t in snap["tables"]:
        tx, ty, tw, th = t["box"]
        for p in t["plates"]:
            assert p["id"].startswith(t["id"] + "-")
            assert p["state"] in ("lleno", "comiendo", "por_terminar", "vacio")
            bx, by, bw, bh = p["bbox"]  # el fake trae cajas: van dentro de la mesa
            assert tx <= bx and bx + bw <= tx + tw + 1e-9
            assert ty <= by and by + bh <= ty + th + 1e-9
    assert sum(snap["counts"].values()) == len(plates)
    json.dumps(snap)  # lo que se manda al navegador


def test_a_failing_view_does_not_stop_the_pipeline():
    def broken(*args):
        raise RuntimeError("se rompió la vista")

    pipeline = Pipeline(FakeClassifier(), ZONES, lambda o: None, on_reading=broken)
    assert len(pipeline.process_frame(FRAME, T0)) == 2


def test_wait_returns_as_soon_as_a_new_reading_arrives():
    feed = SalonFeed("local")
    pipeline = Pipeline(
        FakeClassifier(), ZONES, lambda o: None, on_reading=feed.on_reading
    )
    timer = threading.Timer(0.05, read, args=(pipeline, 1))
    timer.start()
    published = feed.wait_published(0, timeout=5)
    timer.join()
    assert published.version == 1 and published.snapshot is not None
    assert published.frame is None  # sin modo video no se publica el frame
    assert (
        feed.wait_published(1, timeout=0.01).version == 1
    )  # sin lectura nueva, la misma versión


def test_open_alerts_say_who_took_them(tmp_path):
    """F-1b: cada aviso abierto dice quién dijo «Voy» (C-5); null si nadie."""
    from servio_vision.alerts.processor import AlertProcessor
    from servio_vision.salon import build_snapshot
    from tests.test_alert_engine import PROD, obs
    from tests.test_feedback import FakeTelegram, button, tap

    p = AlertProcessor(PROD, FakeTelegram(), tmp_path / "eventos.jsonl")
    try:
        vacios = (("T1-p1", "vacio", "centro"), ("T1-p2", "vacio", "izquierda"))
        p.process(obs(0, *vacios))
        pipeline = Pipeline(FakeClassifier(), ZONES, lambda o: None)
        snap = build_snapshot(pipeline, {}, T0, "casa", p)
        [alert] = snap["alerts"]
        assert (alert["answered_by"], alert["done_at"]) == (None, None)
        tap(p.feedback, button(p.notifier.sent[0][1], "Voy"), who="Juan")
        tap(p.feedback, button(p.notifier.sent[0][1], "Listo"), who="Juan", cid="c2")
        [alert] = build_snapshot(pipeline, {}, T0, "casa", p)["alerts"]
        assert alert["answered_by"] == "Juan"
        assert alert["done_at"] == alert["created_at"]  # «Listo» en la misma lectura
        json.dumps(alert)
        pipeline.close()
    finally:
        p.close()
