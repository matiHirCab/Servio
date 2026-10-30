"""Grabación de los frames procesados (`servio_vision/recording.py`)."""

from datetime import UTC, datetime, timedelta, timezone

import numpy as np

from servio_vision.recording import FrameRecorder, frame_name


def test_frame_name_is_utc_with_microseconds():
    ts = datetime(2026, 10, 28, 20, 30, 15, 250000, tzinfo=UTC)
    assert frame_name(ts) == "20261028T203015.250000Z.jpg"


def test_frame_name_converts_other_timezones_to_utc():
    ts = datetime(2026, 10, 28, 17, 30, 15, tzinfo=timezone(timedelta(hours=-3)))
    assert frame_name(ts) == "20261028T203015.000000Z.jpg"


def test_frame_names_sort_chronologically():
    a = datetime(2026, 1, 1, 0, 0, 1, tzinfo=UTC)
    b = a + timedelta(microseconds=1)
    assert frame_name(a) < frame_name(b)


def test_recorder_creates_the_directory_and_saves_jpegs(tmp_path):
    directory = tmp_path / "grabación" / "sesión"  # tildes y eñes en la ruta (Windows)
    recorder = FrameRecorder(directory)
    frame = np.zeros((8, 8, 3), dtype=np.uint8)

    path = recorder.save(frame, datetime(2026, 10, 28, tzinfo=UTC))

    assert directory.is_dir()
    assert path.parent == directory
    assert path.read_bytes()[:2] == b"\xff\xd8"  # cabecera JPEG
    assert recorder.saved == 1


def test_two_readings_do_not_share_a_file(tmp_path):
    recorder = FrameRecorder(tmp_path)
    frame = np.zeros((8, 8, 3), dtype=np.uint8)
    t = datetime(2026, 10, 28, tzinfo=UTC)

    first = recorder.save(frame, t)
    second = recorder.save(frame, t + timedelta(seconds=30))

    assert first != second
    assert recorder.saved == 2
    assert len(list(tmp_path.glob("*.jpg"))) == 2
