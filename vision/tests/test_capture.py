"""Tests de capture.py con un video y fotos sintéticos (sin cámara)."""

import time
from datetime import UTC, datetime, timedelta

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
