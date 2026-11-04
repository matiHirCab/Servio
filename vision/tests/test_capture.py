"""Tests de capture.py con un video y fotos sintéticos (sin cámara)."""

import threading
import time
from datetime import UTC, datetime, timedelta
from typing import ClassVar

import cv2
import numpy as np
import pytest

from servio_vision.capture import open_source

START = datetime(2026, 10, 17, 20, 0, tzinfo=UTC)


def _make_video(path, seconds: int, fps: int = 10, size=(64, 48)):
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), fps, size)
    assert writer.isOpened()
    for i in range(seconds * fps):
        # el brillo codifica el número de frame, para chequear los saltos
        writer.write(np.full((size[1], size[0], 3), i * 2, dtype=np.uint8))
    writer.release()


def test_video_jumps_by_interval_and_uses_video_time(tmp_path):
    video = tmp_path / "mesa.avi"
    _make_video(video, seconds=5)
    frames = list(open_source(str(video), interval=1.0, start=START))
    assert [ts for _, ts in frames] == [START + timedelta(seconds=s) for s in range(5)]
    # frame 0, 10, 20… → brillo 0, 20, 40…
    brightness = [int(frame.mean()) for frame, _ in frames]
    assert brightness == pytest.approx([0, 20, 40, 60, 80], abs=3)
    assert frames[0][0].shape == (48, 64, 3)


def test_video_speed_paces_playback(tmp_path):
    video = tmp_path / "mesa.avi"
    _make_video(video, seconds=3)
    t0 = time.monotonic()
    frames = list(open_source(str(video), interval=1.0, speed=10, start=START))
    elapsed = time.monotonic() - t0
    assert len(frames) == 3
    assert elapsed >= 0.2  # 2 s de video a 10x ≈ 0,2 s


def test_folder_uses_artificial_ts(tmp_path):
    for name in ["b.png", "a.jpg", "notas.txt"]:
        if name.endswith(".txt"):
            (tmp_path / name).write_text("no es imagen")
        else:
            cv2.imwrite(str(tmp_path / name), np.zeros((10, 20, 3), dtype=np.uint8))
    frames = list(open_source(str(tmp_path), interval=15, start=START))
    assert [ts for _, ts in frames] == [START, START + timedelta(seconds=15)]
    assert frames[0][0].shape == (10, 20, 3)


def test_missing_source():
    with pytest.raises(FileNotFoundError):
        open_source("no/existe.mp4")


def test_invalid_interval(tmp_path):
    with pytest.raises(ValueError):
        open_source(str(tmp_path), interval=0)


def test_frame_seconds_uses_timestamp_and_falls_back_to_fps():
    from servio_vision.capture import frame_seconds

    class Cap:
        def __init__(self, ms):
            self.ms = ms

        def get(self, prop):
            return self.ms

    assert (
        frame_seconds(Cap(12_500), index=7, fps=1000) == 12.5
    )  # fps falsos: manda la marca
    assert frame_seconds(Cap(0), index=0, fps=25) == 0
    assert (
        frame_seconds(Cap(0), index=50, fps=25) == 2
    )  # sin marca de tiempo: cuenta frames


class _FakeCamera:
    """Cámara falsa: cada read() devuelve un frame cuyo valor es el número de frame.
    `fail_after`: después de tantos frames, read() falla siempre (se cortó).
    `fail_once_at`: el read() número N falla una sola vez (paquete atrasado)."""

    instances: ClassVar[list["_FakeCamera"]] = []

    def __init__(self, source, *args, fail_after=None, fail_once_at=None):
        _FakeCamera.instances.append(self)
        self.args = args
        self.count = 0
        self.reads = 0
        self.fail_after = fail_after
        self.fail_once_at = fail_once_at

    def isOpened(self):
        return True

    def get(self, prop):
        return 30.0

    def read(self):
        time.sleep(0.002)
        self.reads += 1
        if self.reads == self.fail_once_at:
            return False, None
        if self.fail_after is not None and self.count >= self.fail_after:
            return False, None
        self.count += 1
        return True, np.full((2, 2, 3), self.count, dtype=np.int32)

    def release(self):
        pass


def _frame_number(frame) -> int:
    return int(frame[0, 0, 0])


def _wait_until(condition, timeout=2.0):
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "la cámara falsa no avanzó a tiempo"
        time.sleep(0.01)


@pytest.fixture
def fake_camera(monkeypatch):
    from servio_vision import capture

    _FakeCamera.instances = []
    monkeypatch.setattr(capture, "CAMERA_WARMUP_S", 0)
    monkeypatch.setattr(capture, "READ_RETRY_S", 0.001)
    monkeypatch.setattr(capture, "CAMERA_RETRY_S", 0.01)
    return lambda **kw: monkeypatch.setattr(
        capture.cv2,
        "VideoCapture",
        lambda source, *args: _FakeCamera(source, *args, **kw),
    )


def test_camera_reconnects_when_stream_drops(fake_camera):
    fake_camera(fail_after=3)  # cada conexión se corta a los 3 frames
    frames = []
    for frame, _ in open_source("0", interval=0.01):
        frames.append(frame)
        if len(frames) == 4:
            break
    assert len(frames) == 4
    assert len(_FakeCamera.instances) >= 2


def test_camera_single_failed_read_does_not_reconnect(fake_camera):
    fake_camera(fail_once_at=3)
    source = open_source("0", interval=0.001)
    next(source)
    _wait_until(lambda: _FakeCamera.instances[0].reads > 5)
    next(source)
    source.close()
    assert len(_FakeCamera.instances) == 1


def test_camera_delivers_latest_frame_while_loop_is_busy(fake_camera):
    fake_camera()
    source = open_source("0", interval=0.001)
    first, _ = next(source)
    # el loop está clasificando; la cámara sigue leyéndose
    camera = _FakeCamera.instances[0]
    _wait_until(lambda: camera.count >= _frame_number(first) + 5)
    second, _ = next(source)
    source.close()
    assert _frame_number(second) >= _frame_number(first) + 5


def test_camera_that_opens_but_sends_no_frames_does_not_start(fake_camera):
    """Una webcam que usa otro programa abre (isOpened) pero no manda frames."""
    fake_camera(fail_after=0)
    with pytest.raises(OSError):
        next(open_source("0", interval=0.01))


def test_a_camera_that_does_not_open_is_a_camera_unavailable_error(fake_camera):
    """Un error propio: `app.run` reintenta este y no otros `OSError` (un archivo
    que falta o el modelo de caras no se arreglan esperando)."""
    from servio_vision.capture import CameraUnavailable

    fake_camera(fail_after=0)
    with pytest.raises(CameraUnavailable):
        next(open_source("0", interval=0.01))


def test_camera_reconnect_deadline_counts_since_the_last_good_frame(
    fake_camera, monkeypatch
):
    """Cada conexión abre y se corta enseguida: con tope, no reconecta para siempre, se da por
    vencida a los `reconnect_s` del último frame bueno."""
    fake_camera(fail_after=1)  # el único frame de cada conexión lo usa la apertura
    done = threading.Event()

    def run():
        list(open_source("0", interval=0.01, reconnect_s=0.3))
        done.set()

    threading.Thread(target=run, daemon=True).start()
    assert done.wait(timeout=3), "la cámara siguió reconectando"
    assert len(_FakeCamera.instances) >= 2


class _OutageCamera(_FakeCamera):
    """Cámara que se corta a los 3 frames y vuelve recién después de `down_opens`
    intentos de abrirla (un corte de Wi-Fi largo)."""

    opens = 0
    down_opens = 0

    def __init__(self, source, *args):
        type(self).opens += 1
        # la primera conexión anda; las siguientes no abren hasta pasado el corte
        self.up = type(self).opens == 1 or type(self).opens > 1 + type(self).down_opens
        super().__init__(source, *args, fail_after=3 if type(self).opens == 1 else None)

    def isOpened(self):
        return self.up

    def read(self):
        return super().read() if self.up else (False, None)


def _outage(monkeypatch, down_opens):
    from servio_vision import capture

    _OutageCamera.opens = 0
    _OutageCamera.down_opens = down_opens
    _FakeCamera.instances = []
    monkeypatch.setattr(capture, "CAMERA_WARMUP_S", 0)
    monkeypatch.setattr(capture, "READ_RETRY_S", 0.001)
    monkeypatch.setattr(capture, "CAMERA_RETRY_S", 0.01)
    monkeypatch.setattr(capture, "CAMERA_RETRY_MAX_S", 0.02)
    monkeypatch.setattr(
        capture.cv2, "VideoCapture", lambda source, *args: _OutageCamera(source, *args)
    )


def test_camera_gives_up_after_the_configured_outage(monkeypatch):
    _outage(monkeypatch, down_opens=1000)  # no vuelve
    done = threading.Event()

    def run():
        list(open_source("0", interval=0.01, reconnect_s=0.2))
        done.set()

    threading.Thread(target=run, daemon=True).start()
    assert done.wait(timeout=3), "la cámara siguió reconectando"


def test_camera_without_limit_survives_a_long_outage(monkeypatch):
    """Un corte más largo que cualquier tope viejo: sin `reconnect_s` no se rinde y
    sigue entregando frames cuando la cámara vuelve."""
    _outage(monkeypatch, down_opens=40)  # ~0,8 s sin cámara con el backoff de arriba
    frames = []
    source = open_source("0", interval=0.01, reconnect_s=None)
    for frame, _ in source:
        frames.append(frame)
        if len(frames) == 6:  # la primera conexión da 2 y el resto, tras el corte
            break
    source.close()
    assert len(frames) == 6
    assert _OutageCamera.opens > 40


def test_camera_retry_waits_grow_up_to_the_cap(monkeypatch):
    from servio_vision import capture

    waits = capture.retry_waits(first=2.0, cap=30.0)
    assert [next(waits) for _ in range(7)] == [2.0, 4.0, 8.0, 16.0, 30.0, 30.0, 30.0]


def test_network_stream_opens_with_timeouts(fake_camera):
    fake_camera()
    source = open_source("http://192.168.0.10:4747/video", interval=0.01)
    next(source)
    source.close()
    backend, params = _FakeCamera.instances[0].args
    assert backend == cv2.CAP_FFMPEG
    assert cv2.CAP_PROP_READ_TIMEOUT_MSEC in params
