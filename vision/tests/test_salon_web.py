"""El servidor de la vista del salón (`salon_web.py`): página, `/estado`, `/eventos`
y `frame.jpg` (modo video, F-1b), con y sin `--web-video`."""

import json
import threading
import urllib.request
from datetime import timedelta
from urllib.error import HTTPError

import pytest

from servio_vision.classifiers.fake import FakeClassifier
from servio_vision.pipeline import Pipeline
from servio_vision.salon import SalonFeed
from servio_vision.salon_web import SalonServer
from tests.test_main import FRAME, T0, ZONES


def read(pipeline: Pipeline, readings: int) -> None:
    for n in range(readings):
        pipeline.process_frame(FRAME, T0 + timedelta(seconds=30 * n))


def serve(feed: SalonFeed):
    srv = SalonServer(feed, "127.0.0.1", 0).start()
    return srv


@pytest.fixture
def server():
    srv = serve(SalonFeed("casa-1"))
    yield srv
    srv.stop()


@pytest.fixture
def video_server():
    srv = serve(SalonFeed("casa-1", video=True))
    yield srv
    srv.stop()


def get(url: str):
    with urllib.request.urlopen(url, timeout=5) as r:
        return r.status, r.headers.get("Content-Type"), r.read()


def status_of(url: str) -> tuple[int, str]:
    try:
        return get(url)[0], ""
    except HTTPError as e:
        return e.code, e.read().decode()


def pipeline_for(server: SalonServer) -> Pipeline:
    return Pipeline(
        FakeClassifier(), ZONES, lambda o: None, on_reading=server.feed.on_reading
    )


def test_server_serves_page_state_and_redirect(server):
    pipeline = pipeline_for(server)
    base = f"http://127.0.0.1:{server.port}"
    assert server.url == f"{base}/casa-1/salon"
    status, kind, body = get(f"{base}/casa-1/salon")
    assert status == 200 and kind.startswith("text/html") and b"Servio" in body
    assert json.loads(get(f"{base}/casa-1/salon/estado")[2]) is None  # sin lecturas
    read(pipeline, 1)
    snap = json.loads(get(f"{base}/casa-1/salon/estado")[2])
    assert snap["version"] == 1
    assert snap["video"] is False  # la página no ofrece el modo video
    status, _, body = get(f"{base}/")  # urllib sigue la redirección
    assert status == 200 and b"Servio" in body


def test_page_has_both_modes_and_asks_for_the_frame(server):
    """La página dibuja la reconstrucción y, si el servidor lo ofrece, el video."""
    _, _, body = get(f"http://127.0.0.1:{server.port}/casa-1/salon")
    page = body.decode()
    assert 'id="floor"' in page and 'id="video"' in page
    assert "frame.jpg" in page
    assert 'id="mode-video"' in page


@pytest.mark.parametrize(
    "path", ["/otra-casa/salon", "/casa-1/cocina", "/casa-1/salon/x"]
)
def test_server_answers_404_elsewhere(server, path):
    with pytest.raises(HTTPError) as e:
        get(f"http://127.0.0.1:{server.port}{path}")
    assert e.value.code == 404


def test_events_stream_pushes_each_new_reading(server):
    pipeline = pipeline_for(server)
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


# ---- modo video (decisión 2: apagado salvo --web-video) ----


def test_frame_is_not_served_without_web_video(server):
    """Sin el flag, el frame no se guarda ni se sirve, aunque el loop lo ofrezca."""
    server.feed.keep_frame(FRAME)
    assert server.feed.frame_jpeg() is None
    code, text = status_of(f"http://127.0.0.1:{server.port}/casa-1/salon/frame.jpg")
    assert code == 404 and "--web-video" in text


def test_frame_is_served_with_web_video_once_there_is_one(video_server):
    base = f"http://127.0.0.1:{video_server.port}/casa-1/salon"
    code, text = status_of(f"{base}/frame.jpg")
    assert code == 404 and "Todavía" in text  # prendido, pero sin lecturas
    video_server.feed.keep_frame(FRAME)
    status, kind, body = get(f"{base}/frame.jpg")
    assert status == 200 and kind == "image/jpeg"
    assert body[:2] == b"\xff\xd8"  # un JPEG
    read(pipeline_for(video_server), 1)
    assert json.loads(get(f"{base}/estado")[2])["video"] is True


def test_only_the_last_frame_is_kept():
    feed = SalonFeed("casa-1", video=True)
    feed.keep_frame(FRAME)
    first = feed.frame_jpeg()
    feed.keep_frame(FRAME + 200)  # otro frame (claro): pisa al anterior
    second = feed.frame_jpeg()
    assert first is not None and second is not None and first != second
