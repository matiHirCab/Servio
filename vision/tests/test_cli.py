"""Flags de la línea de comandos → `Settings` (`servio_vision/cli.py`)."""

import argparse
import logging

import pytest

from servio_vision import cli, settings


def parse(*argv):
    return cli.add_record_flag(cli.build_parser()).parse_args(list(argv))


def test_interval_accepts_one_second_or_more():
    assert cli.interval_seconds("1") == 1.0
    assert cli.interval_seconds("30.5") == 30.5


@pytest.mark.parametrize("raw", ["0", "0.5", "-3"])
def test_interval_below_one_second_is_rejected(raw):
    with pytest.raises(argparse.ArgumentTypeError, match="al menos 1"):
        cli.interval_seconds(raw)


def test_flags_default_to_none_so_they_do_not_override_anything():
    args = parse()
    assert args.source is None
    assert args.interval is None
    assert args.demo is False
    assert args.no_telegram is False
    assert args.record is None


def test_invalid_classifier_is_rejected_by_the_parser():
    with pytest.raises(SystemExit):
        parse("--classifier", "no-existe")


def test_flags_override_environment(monkeypatch):
    monkeypatch.setenv("SERVIO_SOURCE", "desde-entorno.mp4")
    loaded = cli.settings_from_args(parse("--source", "flag.mp4", "--interval", "5"))
    assert str(loaded.source) == "flag.mp4"
    assert loaded.interval == 5


def test_no_telegram_and_demo_flags_apply():
    loaded = cli.settings_from_args(
        parse("--source", "a.mp4", "--demo", "--no-telegram")
    )
    assert loaded.demo is True
    assert loaded.telegram is False


def test_settings_from_args_works_without_record_flag():
    # vista_demo usa el parser sin --record: no tiene que romper
    args = cli.build_parser().parse_args(["--source", "a.mp4"])
    assert cli.settings_from_args(args).record is None


def test_load_settings_activates_the_configuration():
    loaded = cli.load_settings(parse("--source", "a.mp4"))
    assert loaded is not None
    assert settings.current() is loaded


def test_load_settings_without_source_logs_and_returns_none(caplog, monkeypatch):
    monkeypatch.delenv("SERVIO_SOURCE", raising=False)
    # con SERVIO_TEST_TELEGRAM=1 el conftest no aísla el .env real
    monkeypatch.setattr(settings, "ENV_FILE", None)
    with caplog.at_level(logging.ERROR, logger="servio"):
        assert cli.load_settings(parse()) is None
    assert "Falta la fuente" in caplog.text


def test_load_settings_with_missing_config_file_logs_and_returns_none(tmp_path, caplog):
    with caplog.at_level(logging.ERROR, logger="servio"):
        result = cli.load_settings(
            parse("--source", "a.mp4", "--config", str(tmp_path / "no-existe.toml"))
        )
    assert result is None
    assert "Configuración" in caplog.text
