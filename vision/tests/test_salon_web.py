"""El servidor de la vista del salón (`salon_web.py`): página, `/estado`, `/eventos`
y `frame.jpg` (modo video, F-1b), con y sin `--web-video`."""

import json
import threading
import urllib.request
from urllib.error import HTTPError

import pytest

from servio_vision.classifiers.fake import FakeClassifier
from servio_vision.pipeline import Pipeline
from servio_vision.salon import SalonFeed
from servio_vision.salon_web import SalonServer
from tests.test_main import FRAME, T0, ZONES
from tests.test_salon import read


@pytest.fixture
def server():
    srv = SalonServer(SalonFeed("casa-1"), "127.0.0.1", 0).start()
    yield srv
    srv.stop()


@pytest.fixture
def video_server():
    srv = SalonServer(SalonFeed("casa-1", video=True), "127.0.0.1", 0).start()
    yield srv
    srv.stop()


def get(url: str):
    with urllib.request.urlopen(url, timeout=5) as r:
        return r.status, r.headers.get("Content-Type"), r.read()


def get_with_headers(url: str):
    with urllib.request.urlopen(url, timeout=5) as r:
        return r.status, dict(r.headers), r.read()


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
    """Sin el flag, el frame no se guarda ni se sirve, aunque llegue con cada lectura."""
    read(pipeline_for(server), 1)
    assert server.feed.frame_jpeg() is None
    code, text = status_of(f"http://127.0.0.1:{server.port}/casa-1/salon/frame.jpg")
    assert code == 404 and "--web-video" in text


def test_frame_is_served_with_web_video_once_there_is_a_reading(video_server):
    """El frame se publica con la foto de su lectura (`on_reading`): la imagen y las
    capas que la página dibuja encima son del mismo cuadro."""
    base = f"http://127.0.0.1:{video_server.port}/casa-1/salon"
    code, text = status_of(f"{base}/frame.jpg")
    assert code == 404 and "Todavía" in text  # prendido, pero sin lecturas
    read(pipeline_for(video_server), 1)
    status, headers, body = get_with_headers(f"{base}/frame.jpg")
    assert status == 200 and headers["Content-Type"] == "image/jpeg"
    assert body[:2] == bytes([0xFF, 0xD8])  # un JPEG
    # con la versión de su foto: la página lo muestra cuando dibuja esa foto
    assert headers["X-Salon-Version"] == "1"
    assert json.loads(get(f"{base}/estado")[2])["video"] is True


def test_frame_is_encoded_once_per_reading_and_follows_the_snapshot():
    feed = SalonFeed("casa-1", video=True)
    pipeline = Pipeline(
        FakeClassifier(), ZONES, lambda o: None, on_reading=feed.on_reading
    )
    pipeline.process_frame(FRAME, T0)
    first = feed.frame_jpeg()
    assert first is not None and first[1] == 1
    assert feed.frame_jpeg()[0] is first[0]  # cacheado
    pipeline.process_frame(FRAME + 200, T0)  # otro cuadro (más claro)
    second = feed.frame_jpeg()
    assert second is not None and second[1] == 2 and second[0] != first[0]


def test_a_frame_that_cannot_be_encoded_is_logged_once_and_not_served(caplog):
    """Un cuadro roto no puede tirar el loop: la vista queda sin imagen y sigue."""
    import numpy as np

    feed = SalonFeed("casa-1", video=True)
    pipeline = Pipeline(
        FakeClassifier(), ZONES, lambda o: None, on_reading=feed.on_reading
    )
    feed.on_reading(pipeline, [], T0, np.zeros((0, 0, 3), np.uint8))
    with caplog.at_level("WARNING", logger="servio"):
        assert feed.frame_jpeg() is None
        assert feed.frame_jpeg() is None  # se avisa una vez, no en cada pedido
    assert caplog.text.count("No se pudo codificar") == 1
    assert feed.frame_broken  # el servidor lo cuenta distinto de «todavía no hay»
    pipeline.process_frame(FRAME, T0)  # la lectura siguiente vuelve a servirse
    assert feed.frame_jpeg() is not None and not feed.frame_broken


def test_a_broken_frame_is_a_503_not_a_404(video_server):
    import numpy as np

    pipeline = pipeline_for(video_server)
    video_server.feed.on_reading(pipeline, [], T0, np.zeros((0, 0, 3), np.uint8))
    code, text = status_of(
        f"http://127.0.0.1:{video_server.port}/casa-1/salon/frame.jpg"
    )
    assert code == 503 and "no se pudo codificar" in text


def test_server_says_whether_it_is_exposed_to_the_network():
    for host, exposed in (("127.0.0.1", False), ("0.0.0.0", True)):
        srv = SalonServer(SalonFeed("casa-1"), host, 0)
        try:
            assert srv.exposed is exposed
            assert srv.url.startswith("http://localhost" if exposed else "http://127.")
        finally:
            srv.httpd.server_close()


def test_snapshot_carries_the_real_frame_size():
    """Las zonas pueden venir de un video de otra proporción: la página dibuja las
    cajas (normalizadas) sobre la imagen que llega, con el tamaño real del frame."""
    import numpy as np

    feed = SalonFeed("casa-1")
    pipeline = Pipeline(
        FakeClassifier(), ZONES, lambda o: None, on_reading=feed.on_reading
    )
    pipeline.process_frame(np.zeros((300, 400, 3), np.uint8), T0)
    assert feed.snapshot()["frame"] == [400, 300]
    feed.on_reading(pipeline, [], T0, np.zeros((0, 0, 3), np.uint8))
    assert feed.snapshot()["frame"] == [200, 100]  # un frame vacío: las zonas


def test_video_frame_is_served_at_display_size():
    """Una cámara 4K no se manda entera a cada navegador en cada lectura: el frame
    del modo video se sirve a 1280 de lado mayor (las cajas van normalizadas)."""
    import numpy as np

    from servio_vision.salon import display_size

    assert display_size(np.zeros((2160, 3840, 3), np.uint8)).shape == (720, 1280, 3)
    assert display_size(np.zeros((1920, 1080, 3), np.uint8)).shape == (1280, 720, 3)
    small = np.zeros((480, 640, 3), np.uint8)
    assert display_size(small) is small  # chico: tal cual, sin copiar
    feed = SalonFeed("casa-1", video=True)
    pipeline = Pipeline(
        FakeClassifier(), ZONES, lambda o: None, on_reading=feed.on_reading
    )
    pipeline.process_frame(np.zeros((2160, 3840, 3), np.uint8), T0)
    assert feed.snapshot()["frame"] == [3840, 2160]  # la foto dice el tamaño real
    jpeg = feed.frame_jpeg()
    assert jpeg is not None
    import cv2

    served = cv2.imdecode(np.frombuffer(jpeg[0], np.uint8), cv2.IMREAD_COLOR)
    assert served.shape[:2] == (720, 1280)  # misma proporción, menos píxeles
