"""Tests de capture.py con un video y fotos sintéticos (sin cámara)."""

import time
from datetime import UTC, datetime, timedelta

import cv2
import numpy as np
import pytest

from servio_vision import capture
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


class FakeBrokenFpsCapture:
    """Como el .asf de una cámara de seguridad: dice 1000 fps, pero cada frame
    trae su tiempo real (~16 fps) y algunos vienen sin tiempo (0)."""

    def __init__(self, path):
        # frame n a n/16 s; el brillo codifica n
        self.times = [n * 1000 / 16 for n in range(16 * 10)]
        self.times[32] = 0.0  # 2 s: sin tiempo
        self.times[33] = 0.0
        self.pos = -1
        self.released = False

    def isOpened(self):
        return True

    def get(self, prop):
        if prop == cv2.CAP_PROP_FPS:
            return 1000.0
        if prop == cv2.CAP_PROP_POS_MSEC:
            return self.times[self.pos]
        return 0.0

    def grab(self):
        self.pos += 1
        return self.pos < len(self.times)

    def retrieve(self):
        return True, np.full((4, 4, 3), self.pos, dtype=np.uint8)

    def release(self):
        self.released = True


def test_video_with_broken_fps_uses_frame_times(tmp_path, monkeypatch):
    video = tmp_path / "camara.asf"
    video.write_bytes(b"")
    captures = []

    def fake(path):
        captures.append(FakeBrokenFpsCapture(path))
        return captures[-1]

    monkeypatch.setattr(capture.cv2, "VideoCapture", fake)
    frames = list(open_source(str(video), interval=2.5, start=START))
    # una lectura cada 2,5 s de los 10 s del video, con su tiempo real
    assert [ts for _, ts in frames] == [
        START + timedelta(seconds=s) for s in (0, 2.5, 5, 7.5)
    ]
    assert [int(frame[0, 0, 0]) for frame, _ in frames] == [0, 40, 80, 120]
    assert captures[0].released


def test_video_with_broken_fps_does_not_repeat_on_gaps(tmp_path, monkeypatch):
    video = tmp_path / "camara.asf"
    video.write_bytes(b"")

    class Gappy(FakeBrokenFpsCapture):
        def __init__(self, path):
            super().__init__(path)
            # salta de 1 s a 9 s: no tiene que repetir el frame para 2,5, 5 y 7,5
            self.times = [0.0, 500.0, 1000.0, 9000.0, 9500.0]

    monkeypatch.setattr(capture.cv2, "VideoCapture", Gappy)
    frames = list(open_source(str(video), interval=2.5, start=START))
    assert [ts for _, ts in frames] == [START, START + timedelta(seconds=9)]


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
