"""Caras difuminadas apenas se lee el frame (C-3): privacy.py y su enganche en capture."""

import hashlib
from pathlib import Path

import cv2
import numpy as np
import pytest

from servio_vision import privacy
from servio_vision.capture import open_source
from servio_vision.privacy import FaceBlur, PrivacyError, pixelate


class FixedFaces:
    """Detector que devuelve siempre las mismas cajas y cuenta cuántas veces se usó."""

    def __init__(self, boxes):
        self.boxes = boxes
        self.calls = 0

    def detect(self, frame):
        self.calls += 1
        return list(self.boxes)


def noise(height=100, width=200, seed=0) -> np.ndarray:
    return np.random.default_rng(seed).integers(0, 255, (height, width, 3), np.uint8)


def test_pixelate_changes_the_face_and_nothing_else():
    frame = noise()
    original = frame.copy()
    out = pixelate(frame, [(50, 30, 20, 20)])
    assert out is frame  # en el mismo frame
    face = frame[30:50, 50:70]
    assert not np.array_equal(face, original[30:50, 50:70])
    # el mosaico tiene pocos valores distintos: la cara ya no se reconoce
    assert len(np.unique(face.reshape(-1, 3), axis=0)) <= privacy.BLOCKS**2
    # con margen, se pixela alrededor; más allá del margen no se toca nada
    margin = int(20 * privacy.MARGIN)
    untouched = np.ones(frame.shape[:2], bool)
    untouched[30 - margin : 50 + margin, 50 - margin : 70 + margin] = False
    assert np.array_equal(frame[untouched], original[untouched])


def test_pixelate_clips_boxes_at_the_frame_border():
    frame = noise()
    original = frame.copy()
    pixelate(frame, [(190, -5, 30, 30), (-10, 90, 15, 40), (300, 300, 10, 10)])
    assert not np.array_equal(frame[0:10, 190:200], original[0:10, 190:200])
    assert not np.array_equal(frame[90:100, 0:5], original[90:100, 0:5])
    assert np.array_equal(frame[40:60, 80:120], original[40:60, 80:120])


def test_face_blur_counts_faces_and_reuses_boxes_between_detections():
    detector = FixedFaces([(10, 10, 20, 20), (100, 40, 20, 20)])
    blur = FaceBlur(detector, every=3)
    frames = [noise(seed=i) for i in range(6)]
    for frame in frames:
        original = frame.copy()
        blur(frame)
        assert not np.array_equal(frame[10:30, 10:30], original[10:30, 10:30])
    assert detector.calls == 2  # frames 0 y 3
    assert blur.faces == 4


def test_face_blur_refresh_finds_new_faces_without_touching_the_boxes():
    """El frame que va al modelo: entre detecciones se pixeló con cajas viejas; una
    cara que apareció después también se pixela, pero las cajas guardadas y el
    contador no cambian (el frame ya está pixelado: detectar ahí no dice nada)."""
    detector = FixedFaces([(10, 10, 20, 20)])
    blur = FaceBlur(detector, every=3)
    blur(noise(seed=1))  # frame 0: detecta la primera cara
    frame = blur(noise(seed=2))  # frame 1: cajas viejas
    detector.boxes = [(100, 40, 20, 20)]  # "apareció" otra
    before = frame.copy()
    blur.refresh(frame)
    assert detector.calls == 2
    assert not np.array_equal(frame[40:60, 100:120], before[40:60, 100:120])
    assert blur.faces == 1 and blur._boxes == [(10, 10, 20, 20)]
    # el frame 2 sigue pixelando la cara original con las cajas guardadas
    frame = blur(noise(seed=3))
    assert len(np.unique(frame[10:30, 10:30].reshape(-1, 3), axis=0)) <= 36


def test_face_blur_refresh_is_free_right_after_a_detection():
    detector = FixedFaces([(10, 10, 20, 20)])
    blur = FaceBlur(detector)  # every=1: cada frame se detecta
    frame = blur(noise())
    blur.refresh(frame)
    assert detector.calls == 1


def test_privacy_error_is_an_os_error():
    """Quien abre una fuente lo ataja con el mismo `except OSError` que una cámara
    que no abre: sin difuminar no se lee nada."""
    assert issubclass(PrivacyError, OSError)


def test_face_blur_without_faces_leaves_the_frame_alone():
    frame = noise()
    original = frame.copy()
    assert FaceBlur(FixedFaces([]))(frame) is frame
    assert np.array_equal(frame, original)


def test_face_blur_rejects_every_below_one():
    with pytest.raises(ValueError):
        FaceBlur(FixedFaces([]), every=0)


def _write_video(path: Path, frame: np.ndarray, seconds: int = 3, fps: int = 10):
    height, width = frame.shape[:2]
    writer = cv2.VideoWriter(
        str(path), cv2.VideoWriter_fourcc(*"MJPG"), fps, (width, height)
    )
    for _ in range(seconds * fps):
        writer.write(frame)
    writer.release()


def test_open_source_blurs_faces_by_default(tmp_path, monkeypatch):
    """La captura usa el detector por defecto (acá uno falso) sin que nadie lo pida."""
    box = (60, 20, 30, 30)
    monkeypatch.setattr(privacy, "load_detector", lambda: FixedFaces([box]))
    frame = np.zeros((100, 200, 3), np.uint8)
    frame[20:50, 60:90] = (40, 160, 240)  # una "cara" de un color, el resto negro
    frame[20:35, 60:75] = (240, 40, 40)  # con dos mitades para que el mosaico cambie
    _write_video(tmp_path / "salon.avi", frame)
    (tmp_path / "fotos").mkdir()
    cv2.imwrite(str(tmp_path / "fotos" / "a.png"), frame)

    for source in (tmp_path / "salon.avi", tmp_path / "fotos"):
        read, _ = next(open_source(str(source), interval=1))
        face_diff = np.abs(read[20:50, 60:90].astype(int) - frame[20:50, 60:90])
        rest_diff = np.abs(read[60:100, :].astype(int) - frame[60:100, :])
        assert face_diff.mean() > 20, source  # la cara ya no es la cara
        assert rest_diff.mean() < 2, source  # el resto, igual (salvo el códec)


def test_open_source_takes_another_anonymizer(tmp_path, monkeypatch):
    def no_default(*args, **kwargs):
        raise AssertionError("no tendría que armar el anonimizador por defecto")

    monkeypatch.setattr(privacy, "default_anonymizer", no_default)
    _write_video(tmp_path / "salon.avi", noise())
    seen = []

    def mine(frame):
        seen.append(frame)
        return frame

    read, _ = next(open_source(str(tmp_path / "salon.avi"), anonymize=mine))
    assert len(seen) == 1 and seen[0] is read


class _NoisyCamera:
    """Cámara falsa: cada `read()` da el mismo frame de ruido."""

    frame = noise(50, 50)

    def __init__(self, *args):
        pass

    def isOpened(self):
        return True

    def get(self, prop):
        return 30.0

    def read(self):
        return True, self.frame.copy()

    def release(self):
        pass


def test_camera_source_blurs_what_it_delivers(monkeypatch):
    """La cámara también pasa por `open_source`: lo que entrega no tiene caras."""
    from servio_vision import capture

    monkeypatch.setattr(capture, "CAMERA_WARMUP_S", 0)
    monkeypatch.setattr(capture.cv2, "VideoCapture", _NoisyCamera)
    monkeypatch.setattr(privacy, "load_detector", lambda: FixedFaces([(5, 5, 20, 20)]))
    frames = open_source("0", interval=60)
    frame, _ = next(frames)
    frames.close()
    assert not np.array_equal(frame[5:25, 5:25], _NoisyCamera.frame[5:25, 5:25])
    assert np.array_equal(frame[40:50, 40:50], _NoisyCamera.frame[40:50, 40:50])


def test_ensure_model_downloads_and_checks_the_hash(tmp_path, monkeypatch):
    payload = b"modelo falso"
    monkeypatch.setattr(privacy, "MODEL_PATH", tmp_path / "models" / "yunet.onnx")
    monkeypatch.setattr(privacy, "MODEL_SHA256", hashlib.sha256(payload).hexdigest())

    def fake_download(url, destination):
        assert url == privacy.MODEL_URL
        Path(destination).write_bytes(payload)

    monkeypatch.setattr(privacy, "_download", fake_download)
    path = privacy.ensure_model(privacy.MODEL_PATH)
    assert path.read_bytes() == payload
    assert sorted(path.parent.iterdir()) == [path]  # sin .part ni temporales

    # la segunda vez no baja nada
    monkeypatch.setattr(privacy, "_download", None)
    assert privacy.ensure_model(privacy.MODEL_PATH) == path


def test_ensure_model_replaces_a_corrupt_default(tmp_path, monkeypatch):
    payload = b"modelo bueno"
    path = tmp_path / "yunet.onnx"
    path.write_bytes(b"trunc")
    monkeypatch.setattr(privacy, "MODEL_PATH", path)
    monkeypatch.setattr(privacy, "MODEL_SHA256", hashlib.sha256(payload).hexdigest())
    monkeypatch.setattr(privacy, "_download", lambda url, dst: dst.write_bytes(payload))
    assert privacy.ensure_model(path).read_bytes() == payload
    # otro modelo en FACE_MODEL se acepta tal cual, sin hash
    other = tmp_path / "otro.onnx"
    other.write_bytes(b"lo que sea")
    assert privacy.ensure_model(other) == other


def test_ensure_model_rejects_a_wrong_hash(tmp_path, monkeypatch):
    monkeypatch.setattr(privacy, "MODEL_PATH", tmp_path / "yunet.onnx")
    monkeypatch.setattr(
        privacy, "_download", lambda url, dst: Path(dst).write_bytes(b"otra cosa")
    )
    with pytest.raises(PrivacyError, match="sha256"):
        privacy.ensure_model(privacy.MODEL_PATH)
    assert not list(tmp_path.iterdir())  # no queda el archivo malo


def test_ensure_model_explains_when_offline(tmp_path, monkeypatch):
    monkeypatch.setattr(privacy, "MODEL_PATH", tmp_path / "yunet.onnx")

    def offline(url, dst):
        raise OSError("sin red")

    monkeypatch.setattr(privacy, "_download", offline)
    with pytest.raises(PrivacyError, match="Sin internet"):
        privacy.ensure_model(privacy.MODEL_PATH)


def test_download_has_a_timeout(tmp_path, monkeypatch):
    """Una red con portal cautivo no tiene que colgar el arranque."""
    seen = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self, size=-1):
            return b""

    def fake_urlopen(url, timeout=None):
        seen["timeout"] = timeout
        return Response()

    monkeypatch.setattr(privacy.urllib.request, "urlopen", fake_urlopen)
    privacy._download("http://x", tmp_path / "m.onnx")
    assert seen["timeout"] == privacy.DOWNLOAD_TIMEOUT_S


def test_detector_handles_an_empty_frame(monkeypatch):
    """Una cámara que se cortó puede dar un frame de 0×0: no es un error del detector."""

    class Net:
        def setInputSize(self, size):
            pass

        def detect(self, frame):
            raise AssertionError("no tendría que llamar al detector")

    detector = privacy.YuNetDetector.__new__(privacy.YuNetDetector)
    detector._net, detector._size, detector.detect_width = Net(), None, 1280
    assert detector.detect(np.zeros((0, 0, 3), np.uint8)) == []


def test_detector_failure_is_a_privacy_error():
    class BrokenNet:
        def setInputSize(self, size):
            pass

        def detect(self, frame):
            raise cv2.error("roto")

    detector = privacy.YuNetDetector.__new__(privacy.YuNetDetector)
    detector._net, detector._size, detector.detect_width = BrokenNet(), None, 1280
    with pytest.raises(PrivacyError, match="detector de caras"):
        detector.detect(np.zeros((10, 10, 3), np.uint8))


def test_model_path_from_env(tmp_path):
    assert privacy.model_path({}) == privacy.MODEL_PATH
    assert privacy.model_path({privacy.MODEL_ENV: str(tmp_path / "m.onnx")}) == (
        tmp_path / "m.onnx"
    )
    # relativa: desde la raíz del repo (como YOLO_WEIGHTS en .env.example), no del cwd
    assert privacy.model_path({privacy.MODEL_ENV: "vision/models/m.onnx"}) == (
        privacy.VISION_DIR.parent / "vision" / "models" / "m.onnx"
    )
    with pytest.raises(PrivacyError, match="FACE_MODEL"):
        privacy.ensure_model(tmp_path / "no_existe.onnx")


def test_open_source_reads_a_single_photo_blurred(tmp_path, monkeypatch):
    """Una foto suelta también es una fuente: un frame, con las caras pixeladas (así
    draw_zones y la prueba manual no necesitan un camino propio)."""
    monkeypatch.setattr(privacy, "load_detector", lambda: FixedFaces([(0, 0, 40, 40)]))
    frame = noise()
    cv2.imwrite(str(tmp_path / "salon.png"), frame)
    frames = list(open_source(str(tmp_path / "salon.png")))
    assert len(frames) == 1
    read, _ = frames[0]
    assert not np.array_equal(read[0:40, 0:40], frame[0:40, 0:40])
    assert np.array_equal(read[60:100, 100:200], frame[60:100, 100:200])


def test_read_camera_detects_only_on_delivered_frames(monkeypatch):
    """La cámara da ~30 fps y el loop usa uno cada `interval`: el detector corre solo
    sobre el que se entrega, no sobre los que se descartan."""
    from servio_vision import capture

    monkeypatch.setattr(capture, "CAMERA_WARMUP_S", 0)
    monkeypatch.setattr(capture.cv2, "VideoCapture", _NoisyCamera)
    detector = FixedFaces([(5, 5, 20, 20)])
    frames = open_source("0", interval=60, anonymize=FaceBlur(detector))
    frame, _ = next(frames)
    import time

    time.sleep(0.05)  # la cámara sigue produciendo frames que nadie pide
    frames.close()
    assert detector.calls == 1
    assert not np.array_equal(frame[5:25, 5:25], _NoisyCamera.frame[5:25, 5:25])


def test_detector_keeps_big_faces_and_drops_only_giant_boxes():
    """Alguien parado debajo de la cámara tiene una cara grande: se pixela igual.
    Solo se descarta lo que ocupa más de la mitad del frame (un torso, una mesa)."""

    class Net:
        def __init__(self, faces):
            self.faces = faces

        def setInputSize(self, size):
            pass

        def detect(self, frame):
            return 1, np.array(self.faces, np.float32)

    detector = privacy.YuNetDetector.__new__(privacy.YuNetDetector)
    detector.detect_width, detector._size = 1280, None
    # frame 1280×720: una cara de 200 px (28 %) pasa; una "cara" de 400 px (56 %) no
    detector._net = Net([[100, 100, 150, 200, 0.9], [600, 100, 300, 400, 0.9]])
    boxes = detector.detect(np.zeros((720, 1280, 3), np.uint8))
    assert [b[3] for b in boxes] == [201]


def test_frame_recorder_writes_non_ascii_paths(tmp_path):
    """En Windows, cv2.imwrite no escribe rutas con tildes: `out/grabación` tiene que
    funcionar igual."""
    from datetime import UTC, datetime

    from servio_vision.recording import FrameRecorder

    recorder = FrameRecorder(tmp_path / "grabación")
    path = recorder.save(
        noise(), datetime(2026, 10, 28, 20, 30, 15, 250000, tzinfo=UTC)
    )
    assert path.name == "20261028T203015.250Z.jpg"
    assert cv2.imdecode(np.fromfile(path, np.uint8), cv2.IMREAD_COLOR).shape == (
        100,
        200,
        3,
    )


@pytest.mark.skipif(
    not privacy.MODEL_PATH.exists(), reason="sin el modelo de YuNet en vision/models/"
)
def test_yunet_runs_on_a_frame_without_faces():
    detector = privacy.YuNetDetector(privacy.MODEL_PATH)
    assert detector.detect(np.zeros((360, 640, 3), np.uint8)) == []
