"""Salón en vivo (F-1): la foto que arma `salon.py` y el servidor de `salon_web.py`."""

import json
import threading
import urllib.request
from datetime import timedelta
from urllib.error import HTTPError

import pytest

from servio_vision.classifiers.fake import FakeClassifier
from servio_vision.pipeline import Pipeline
from servio_vision.salon import SalonFeed, plate_box, table_box
from servio_vision.salon_web import SalonServer
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
    version, snap = feed.wait(0, timeout=5)
    timer.join()
    assert version == 1 and snap is not None
    assert feed.wait(1, timeout=0.01)[0] == 1  # sin lectura nueva, la misma versión


@pytest.fixture
def server():
    feed = SalonFeed("casa-1")
    srv = SalonServer(feed, "127.0.0.1", 0).start()
    yield srv
    srv.stop()


def get(url: str):
    with urllib.request.urlopen(url, timeout=5) as r:
        return r.status, r.headers.get("Content-Type"), r.read()


def test_server_serves_page_state_and_redirect(server):
    pipeline = Pipeline(
        FakeClassifier(), ZONES, lambda o: None, on_reading=server.feed.on_reading
    )
    base = f"http://127.0.0.1:{server.port}"
    assert server.url == f"{base}/casa-1/salon"
    status, kind, body = get(f"{base}/casa-1/salon")
    assert status == 200 and kind.startswith("text/html") and b"Servio" in body
    assert json.loads(get(f"{base}/casa-1/salon/estado")[2]) is None  # sin lecturas
    read(pipeline, 1)
    assert json.loads(get(f"{base}/casa-1/salon/estado")[2])["version"] == 1
    status, _, body = get(f"{base}/")  # urllib sigue la redirección
    assert status == 200 and b"Servio" in body


@pytest.mark.parametrize(
    "path", ["/otra-casa/salon", "/casa-1/cocina", "/casa-1/salon/x"]
)
def test_server_answers_404_elsewhere(server, path):
    with pytest.raises(HTTPError) as e:
        get(f"http://127.0.0.1:{server.port}{path}")
    assert e.value.code == 404


def test_events_stream_pushes_each_new_reading(server):
    pipeline = Pipeline(
        FakeClassifier(), ZONES, lambda o: None, on_reading=server.feed.on_reading
    )
    read(pipeline, 1)
    url = f"http://127.0.0.1:{server.port}/casa-1/salon/eventos"
    with urllib.request.urlopen(url, timeout=5) as r:
        assert r.headers.get("Content-Type") == "text/event-stream"
        first = json.loads(r.readline().decode().removeprefix("data: "))
        assert first["version"] == 1
        r.readline()  # línea en blanco que cierra el evento
        threading.Timer(0.05, read, args=(pipeline, 1)).start()
        second = json.loads(r.readline().decode().removeprefix("data: "))
        assert second["version"] == 2
