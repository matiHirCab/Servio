"""Configuración en un solo lugar (A-1): settings.py, el toml de la casa y los flags."""

from pathlib import Path

import pytest

import main as loop
from servio_vision.cli import build_parser, settings_from_args
from servio_vision.settings import REPO_DIR, Settings
from servio_vision.zones import TableZone, Zones, save_zones


def test_defaults_without_env_or_toml(monkeypatch):
    for name in ("CLAUDE_PROVIDER", "SMOOTH_READINGS", "DEMO_MODE", "ALERT_T1"):
        monkeypatch.delenv(name, raising=False)
    cfg = Settings.load()
    assert cfg.source is None and cfg.classifier == "fake" and cfg.interval == 30
    assert cfg.claude_provider == "cli" and cfg.smooth_readings == 2
    assert cfg.demo is False and cfg.telegram is True and cfg.alert_t1 is None
    assert cfg.model_for() == "haiku" and cfg.timeout_for("openai") == 14.0
    assert cfg.prompt_file_for("openai").name == "openai.json"


def test_env_values_and_empty_ones(monkeypatch):
    monkeypatch.setenv("CLAUDE_PROVIDER", "openai")
    monkeypatch.setenv("CLAUDE_MODEL", "")  # como `CLAUDE_MODEL=` en el .env
    monkeypatch.setenv("OPENAI_MODEL", "gpt-otro")
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.setenv("SMOOTH_MIN_CONFIDENCE", "0.6")
    cfg = Settings.load()
    assert cfg.claude_provider == "openai" and cfg.claude_model is None
    assert cfg.model_for() == "gpt-otro"
    assert cfg.demo is True and cfg.smooth_min_confidence == 0.6


def test_env_file_is_loaded_and_does_not_override_the_environment(
    tmp_path, monkeypatch
):
    env = tmp_path / ".env"
    env.write_text("ALERT_T2=120\nCLAUDE_PROVIDER=anthropic\n", encoding="utf-8")
    monkeypatch.delenv("ALERT_T2", raising=False)
    monkeypatch.setenv("CLAUDE_PROVIDER", "cli")
    cfg = Settings.load(env_file=env)
    assert cfg.alert_t2 == 120
    assert cfg.claude_provider == "cli"  # el entorno ya puesto gana al .env


def test_toml_beats_env_and_flags_beat_toml(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_PROVIDER", "cli")
    monkeypatch.setenv("SMOOTH_READINGS", "4")
    toml = tmp_path / "casa.toml"
    toml.write_text(
        """
        source = "http://192.168.0.5:4747/video"
        interval = 10
        classifier = "hybrid"

        [modelo]  # las tablas se aplanan: solo agrupan
        claude_provider = "anthropic"

        [avisos]
        demo = true
        alert_t1 = 90
        """,
        encoding="utf-8",
    )
    cfg = Settings.load(toml, interval=5, demo=None)
    assert cfg.source == "http://192.168.0.5:4747/video"
    assert cfg.interval == 5  # el flag pisa al toml
    assert cfg.classifier == "hybrid" and cfg.claude_provider == "anthropic"
    assert cfg.demo is True and cfg.alert_t1 == 90  # None en un flag no pisa nada
    assert cfg.smooth_readings == 4  # lo que el toml no dice sale del entorno


def test_bad_toml_and_bad_values_explain(tmp_path):
    with pytest.raises(FileNotFoundError, match="No existe"):
        Settings.load(tmp_path / "no.toml")
    broken = tmp_path / "roto.toml"
    broken.write_text("interval = \n", encoding="utf-8")
    with pytest.raises(ValueError, match="TOML"):
        Settings.load(broken)
    with pytest.raises(ValueError):  # pydantic: fuera de rango
        Settings.load(smooth_min_confidence=2)


def test_repo_files_resolve_from_the_repo_root(monkeypatch):
    monkeypatch.setenv("FACE_MODEL", "vision/models/m.onnx")
    monkeypatch.setenv("CLAUDE_PROMPT_FILE", str(Path("/abs/prompt.txt").resolve()))
    cfg = Settings.load()
    assert cfg.face_model == REPO_DIR / "vision" / "models" / "m.onnx"
    assert cfg.claude_prompt_file == Path("/abs/prompt.txt").resolve()


def test_house_keys_do_not_leak_from_generic_env_vars(monkeypatch):
    """`SOURCE` o `RECORD` de la máquina no son de Servio; `SERVIO_SOURCE` sí."""
    monkeypatch.setenv("SOURCE", "x")
    monkeypatch.setenv("RECORD", "y")
    monkeypatch.setenv("INTERVAL", "7")
    cfg = Settings.load()
    assert cfg.source is None and cfg.record is None and cfg.interval == 30
    monkeypatch.setenv("SERVIO_SOURCE", "0")
    assert Settings.load().source == "0"
    assert (
        Settings.load(source="video.mp4").source == "video.mp4"
    )  # flag/toml sin prefijo


def test_flags_only_override_what_was_passed(monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "true")
    args = build_parser().parse_args(["--source", "0", "--interval", "15"])
    cfg = settings_from_args(args)
    assert cfg.source == "0" and cfg.interval == 15
    assert cfg.demo is True  # no pasar --demo no apaga el DEMO_MODE del entorno
    assert cfg.telegram is True
    args = build_parser().parse_args(["--no-telegram", "--demo"])
    cfg = settings_from_args(args)
    assert cfg.telegram is False and cfg.demo is True and cfg.source is None


ZONES = Zones(
    frame_size=(200, 100),
    tables=[
        TableZone(table_id="T1", name="Mesa 1", polygon=[(0, 0), (90, 0), (90, 90)])
    ],
)


def test_main_runs_from_a_toml_without_other_flags(tmp_path, monkeypatch, caplog):
    """Hecho cuando: `python main.py --config casa.toml` arranca sin otros flags."""
    import cv2
    import numpy as np

    monkeypatch.delenv("DEMO_MODE", raising=False)
    video = tmp_path / "salon.avi"
    writer = cv2.VideoWriter(
        str(video), cv2.VideoWriter_fourcc(*"MJPG"), 10, (200, 100)
    )
    for _ in range(10 * 35):
        writer.write(np.zeros((100, 200, 3), np.uint8))
    writer.release()
    zones = tmp_path / "zones.json"
    save_zones(ZONES, zones)
    toml = tmp_path / "casa.toml"
    toml.write_text(
        f"source = {str(video)!r}\nzones = {str(zones)!r}\n"
        'classifier = "fake"\ninterval = 30\ntelegram = false\n',
        encoding="utf-8",
    )
    log = tmp_path / "eventos.jsonl"
    monkeypatch.setattr(loop.alerts, "DEFAULT_LOG", log)
    caplog.set_level("INFO")
    assert loop.main(["--config", str(toml)]) == 0
    loop.alerts._processor.close()
    assert "2 lecturas" in caplog.text  # a los 0 y 30 s


def test_main_explains_missing_source_and_bad_config(tmp_path, caplog):
    caplog.set_level("ERROR")
    assert loop.main([]) == 2
    assert "Falta la fuente" in caplog.text
    assert loop.main(["--config", str(tmp_path / "no.toml")]) == 2
    assert "No existe la configuración" in caplog.text
