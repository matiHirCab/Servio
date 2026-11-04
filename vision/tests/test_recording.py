"""Grabar y reproducir sesiones (B-1): --record deja una sesión completa y
--source DIR la reproduce con las mismas horas, las mismas reglas y los mismos avisos."""

import json
import sys
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import cv2
import numpy as np
import pytest

import main as loop
from servio_vision import alerts, app, cli, recording
from servio_vision.capture import open_source
from servio_vision.recording import FrameRecorder, frame_name
from servio_vision.zones import TableZone, Zones, save_zones

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import comparar_sesion

ZONES = Zones(
    frame_size=(200, 100),
    tables=[
        TableZone(table_id="T1", name="Mesa 1", polygon=[(0, 0), (90, 0), (90, 90)]),
        TableZone(
            table_id="T2", name="Mesa 2", polygon=[(110, 0), (199, 0), (199, 90)]
        ),
    ],
)


def _video(tmp_path, seconds=400) -> Path:
    video = tmp_path / "salon.avi"
    writer = cv2.VideoWriter(str(video), cv2.VideoWriter_fourcc(*"MJPG"), 5, (200, 100))
    frame = np.full((100, 200, 3), 90, np.uint8)
    for _ in range(5 * seconds):
        writer.write(frame)
    writer.release()
    return video


def _run(tmp_path, monkeypatch, log: Path, *args) -> int:
    monkeypatch.delenv("DEMO_MODE", raising=False)
    monkeypatch.setattr(alerts, "DEFAULT_LOG", log)
    monkeypatch.setattr(alerts, "_processor", None)
    code = loop.main(["--no-telegram", *args])
    if alerts._processor is not None:
        alerts._processor.close()
    return code


def test_record_leaves_a_complete_session_and_replay_gives_the_same_alerts(
    tmp_path, monkeypatch
):
    """Hecho cuando: al reproducir salen los mismos avisos que en vivo. Acá con el
    clasificador falso (determinista), que es lo que el test puede garantizar."""
    zones = tmp_path / "zones.json"
    save_zones(ZONES, zones)
    session = tmp_path / "sesion"
    live_log = tmp_path / "vivo.jsonl"
    code = _run(
        tmp_path,
        monkeypatch,
        live_log,
        "--source",
        str(_video(tmp_path)),
        "--zones",
        str(zones),
        "--classifier",
        "fake",
        "--interval",
        "30",
        "--demo",
        "--record",
        str(session),
    )
    assert code == 0
    # la sesión: frames con su hora, sesion.json y los eventos de la corrida
    frames = recording.session_frames(session)
    assert len(frames) == 14  # 400 s cada 30 s
    assert all(ts.tzinfo is UTC for _, ts in frames)
    info = recording.read_info(session)
    assert info["rules"]["interval"] == 30 and info["readings"] == 14
    assert info["rules"]["demo"] is True and info["rules"]["finished_after"] > 0
    assert info["zones"]["tables"][0]["table_id"] == "T1" and "ended_at" in info
    recorded = (session / recording.EVENTS_FILE).read_text("utf-8").splitlines()
    assert recorded[0].startswith('{"event": "run_started"')
    assert info["anonymized"] is True
    assert recording.alert_signature(recorded)  # con --demo el fake dispara avisos

    # reproducir: sin --zones, --interval ni --demo, salen de la sesión (con los
    # umbrales de producción los avisos no coincidirían)
    replay_log = tmp_path / "replay.jsonl"
    code = _run(
        tmp_path,
        monkeypatch,
        replay_log,
        "--source",
        str(session),
        "--classifier",
        "fake",
    )
    assert code == 0
    assert comparar_sesion.compare(recorded, recording.last_run_lines(replay_log)) == []
    # la reproducción deja sus eventos en la sesión: es lo que compara la herramienta
    assert (session / recording.REPLAY_EVENTS_FILE).exists()
    assert comparar_sesion.main([str(session)]) == 0
    assert comparar_sesion.main([str(session), "--log", str(replay_log)]) == 0
    # la misma carpeta no se graba dos veces: quedarían dos sesiones mezcladas
    code = _run(
        tmp_path,
        monkeypatch,
        replay_log,
        "--source",
        str(session),
        "--record",
        str(session),
    )
    assert code == 2 and recording.read_info(session)["readings"] == 14


def _settings(*flags):
    return cli.settings_from_args(cli.build_parser().parse_args(list(flags)))


def test_session_settings_restore_the_recorded_rules(tmp_path):
    session = tmp_path / "s"
    session.mkdir()
    (session / recording.SESSION_FILE).write_text(
        json.dumps(
            {"rules": {"interval": 20, "demo": True, "finished_after": 90, "otra": 1}}
        ),
        "utf-8",
    )
    settings = _settings("--source", str(session), "--interval", "5")
    restored = app.session_settings(settings)
    assert (restored.interval, restored.demo, restored.finished_after) == (
        20.0,
        True,
        90,
    )
    # una reproducción no manda a Telegram ni escribe en la base compartida
    assert restored.telegram is False
    scratch = (session / app.REPLAY_DB).resolve().as_posix()
    assert restored.database_url == f"sqlite:///{scratch}"
    assert (
        app.session_settings(_settings("--source", "v.mp4", "--interval", "5")).interval
        == 5
    )
    (session / recording.SESSION_FILE).write_text('{"rules": {"demo": true}}', "utf-8")
    with pytest.raises(OSError, match="sin cadencia"):
        app.session_settings(settings)
    # un null grabado vuelve a ser "por defecto" aunque los flags digan otra cosa
    (session / recording.SESSION_FILE).write_text(
        '{"rules": {"interval": 20, "finished_after": null}}', "utf-8"
    )
    flagged = _settings("--source", str(session), "--finished-after", "40")
    assert app.session_settings(flagged).finished_after is None
    # editado a mano con un valor inválido: error de config, no un traceback después
    (session / recording.SESSION_FILE).write_text(
        '{"rules": {"interval": 0.5}}', "utf-8"
    )
    with pytest.raises(OSError, match="regla inválida"):
        app.session_settings(settings)
    (session / recording.SESSION_FILE).write_text(
        '{"rules": {"interval": 10}, "zones": {"x": 1}}', "utf-8"
    )
    with pytest.raises(OSError, match="zonas inválidas"):
        recording.session_zones(session)


def test_recording_into_the_log_folder_is_refused(tmp_path, monkeypatch):
    log = tmp_path / "vivo.jsonl"  # grabar en tmp_path pisaría este log con la copia
    code = _run(
        tmp_path, monkeypatch, log, "--source", "no.mp4", "--record", str(tmp_path)
    )
    assert code == 2
    assert not (tmp_path / recording.EVENTS_FILE).exists()


def test_session_info_hides_camera_credentials():
    assert (
        recording.redact_source("rtsp://admin:S3cret@192.168.1.10/stream")
        == "rtsp://192.168.1.10/stream"
    )
    assert recording.redact_source("video.mp4") == "video.mp4"
    assert (
        recording.redact_source("http://cam.local:8080/video")
        == "http://cam.local:8080/video"
    )


def test_a_run_that_never_read_a_frame_leaves_no_session(tmp_path, monkeypatch):
    record = tmp_path / "fallida"
    log = tmp_path / "log.jsonl"
    code = _run(
        tmp_path, monkeypatch, log, "--source", "no.mp4", "--record", str(record)
    )
    assert code == 2
    assert not recording.is_session(record)  # sin frames no hay sesión
    recording.ensure_free(record)  # y la carpeta se puede usar para grabar de verdad


def test_last_run_lines_parse_the_events(tmp_path):
    log = tmp_path / "eventos.jsonl"
    old = json.dumps({"event": "run_started", "n": 1})
    # un formato distinto (claves en otro orden, sin espacios) sigue marcando la corrida
    new = json.dumps({"n": 2, "event": "run_started"}, separators=(",", ":"))
    obs = json.dumps({"event": "observation", "table_id": "T1"})
    lines = [old, obs, "{ roto", new, obs]
    log.write_text("".join(line + "\n" for line in lines), "utf-8")
    assert recording.last_run_lines(log) == [new, obs]
    assert recording.last_run_lines(tmp_path / "no-existe.jsonl") == []


def test_session_frames_keep_their_real_times(tmp_path):
    session = tmp_path / "s"
    session.mkdir()
    (session / recording.SESSION_FILE).write_text(
        '{"rules": {"interval": 10}, "anonymized": true}', "utf-8"
    )
    t0 = datetime(2026, 11, 4, 21, 0, tzinfo=UTC)
    times = [t0, t0.replace(second=12), t0.replace(second=41)]  # cadencia irregular
    for t in times:
        cv2.imwrite(
            str(session / recording.frame_name(t)), np.zeros((20, 20, 3), np.uint8)
        )
    (session / "notas.jpg").write_bytes(b"no es un frame")
    frames = list(open_source(str(session), interval=999))
    assert [ts for _, ts in frames] == times  # las horas grabadas, no cada 999 s
    # ya grabados sin caras: salen tal cual, sin pasar otra vez por el difuminado
    assert all((frame == 0).all() for frame, _ in frames)
    assert recording.is_session(session) and not recording.is_session(tmp_path)


def test_compare_explains_differences():
    a = json.dumps(
        {"event": "alert_opened", "type": "mesa_terminada", "table_id": "T1", "ts": "t"}
    )
    b = json.dumps(
        {"event": "alert_opened", "type": "plato_vacio", "table_id": "T1", "ts": "t"}
    )
    assert comparar_sesion.compare([a], [a]) == []
    diffs = comparar_sesion.compare([a], [b])
    assert any("grabado y no reproducido" in d for d in diffs)
    assert any("reproducido y no grabado" in d for d in diffs)
    assert comparar_sesion.compare([a, b], [b, a]) == [
        "los mismos avisos, en otro orden"
    ]
    # uno de más de un lado no es "otro orden"
    assert comparar_sesion.compare([a, a, b], [a, b]) == [
        "grabado y no reproducido: alert_opened · mesa_terminada · T1 · t"
    ]


def test_session_without_frames_or_info_explains(tmp_path):
    empty = tmp_path / "vacia"
    empty.mkdir()
    (empty / recording.SESSION_FILE).write_text("{}", "utf-8")
    with pytest.raises(FileNotFoundError, match="no tiene frames"):
        recording.session_frames(empty)
    (empty / recording.SESSION_FILE).write_text("{ roto", "utf-8")
    with pytest.raises(OSError, match="no se puede leer"):
        recording.read_info(empty)
    (empty / recording.SESSION_FILE).write_text("[1, 2]", "utf-8")
    with pytest.raises(OSError, match="no es un objeto"):
        recording.read_info(empty)
    # un aviso al que le falta un campo se saltea, no rompe la comparación
    bad = json.dumps({"event": "alert_opened", "type": "plato_vacio", "ts": "t"})
    assert recording.alert_signature([bad]) == []


# ---- FrameRecorder, la base de la sesión ----


def test_frame_name_is_utc_with_microseconds():
    ts = datetime(2026, 10, 28, 20, 30, 15, 250000, tzinfo=UTC)
    assert frame_name(ts) == "20261028T203015.250000Z.jpg"
    assert recording.frame_time(Path(frame_name(ts))) == ts


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
