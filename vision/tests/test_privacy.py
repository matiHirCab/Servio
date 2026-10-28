"""Caras difuminadas apenas se lee el frame (C-3): privacy.py y su enganche en capture."""

import hashlib
from pathlib import Path

import cv2
import numpy as np
import pytest

from servio_vision import privacy
from servio_vision.capture import CameraReader, open_source
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


def test_camera_reader_blurs_in_its_thread(monkeypatch):
    """Los frames de la cámara salen del hilo ya pixelados: `latest()` nunca ve una
    cara (tampoco `vista_demo`, que usa `CameraReader` directo)."""
    from servio_vision import capture

    monkeypatch.setattr(capture, "CAMERA_WARMUP_S", 0)
    monkeypatch.setattr(capture.cv2, "VideoCapture", _NoisyCamera)
    monkeypatch.setattr(privacy, "load_detector", lambda: FixedFaces([(5, 5, 20, 20)]))
    reader = CameraReader(0)
    reader.start()
    try:
        frame = reader.latest()
    finally:
        reader.stop()
    assert frame is not None
    assert not np.array_equal(frame[5:25, 5:25], _NoisyCamera.frame[5:25, 5:25])
    assert np.array_equal(frame[40:50, 40:50], _NoisyCamera.frame[40:50, 40:50])
    assert reader.anonymize.faces >= 1


def test_ensure_model_downloads_and_checks_the_hash(tmp_path, monkeypatch):
    payload = b"modelo falso"
    monkeypatch.setattr(privacy, "MODEL_PATH", tmp_path / "models" / "yunet.onnx")
    monkeypatch.setattr(privacy, "MODEL_SHA256", hashlib.sha256(payload).hexdigest())

    def fake_download(url, destination):
        assert url == privacy.MODEL_URL
        Path(destination).write_bytes(payload)

    monkeypatch.setattr(privacy.urllib.request, "urlretrieve", fake_download)
    path = privacy.ensure_model(privacy.MODEL_PATH)
    assert path.read_bytes() == payload
    assert not path.with_suffix(".part").exists()

    # la segunda vez no baja nada
    monkeypatch.setattr(privacy.urllib.request, "urlretrieve", None)
    assert privacy.ensure_model(privacy.MODEL_PATH) == path


def test_ensure_model_rejects_a_wrong_hash(tmp_path, monkeypatch):
    monkeypatch.setattr(privacy, "MODEL_PATH", tmp_path / "yunet.onnx")
    monkeypatch.setattr(
        privacy.urllib.request,
        "urlretrieve",
        lambda url, dst: Path(dst).write_bytes(b"otra cosa"),
    )
    with pytest.raises(PrivacyError, match="sha256"):
        privacy.ensure_model(privacy.MODEL_PATH)
    assert not list(tmp_path.iterdir())  # no queda el archivo malo


def test_ensure_model_explains_when_offline(tmp_path, monkeypatch):
    monkeypatch.setattr(privacy, "MODEL_PATH", tmp_path / "yunet.onnx")

    def offline(url, dst):
        raise OSError("sin red")

    monkeypatch.setattr(privacy.urllib.request, "urlretrieve", offline)
    with pytest.raises(PrivacyError, match="Sin internet"):
        privacy.ensure_model(privacy.MODEL_PATH)


def test_model_path_from_env(tmp_path):
    assert privacy.model_path({}) == privacy.MODEL_PATH
    assert privacy.model_path({privacy.MODEL_ENV: str(tmp_path / "m.onnx")}) == (
        tmp_path / "m.onnx"
    )
    with pytest.raises(PrivacyError, match="FACE_MODEL"):
        privacy.ensure_model(tmp_path / "no_existe.onnx")


@pytest.mark.skipif(
    not privacy.MODEL_PATH.exists(), reason="sin el modelo de YuNet en vision/models/"
)
def test_yunet_runs_and_filters_giant_boxes():
    """Con el modelo real: corre sobre un frame sin caras y la caja gigante no pasa."""
    detector = privacy.YuNetDetector(privacy.MODEL_PATH)
    assert detector.detect(np.zeros((360, 640, 3), np.uint8)) == []
    assert detector.detect(np.zeros((1080, 1920, 3), np.uint8)) == []
