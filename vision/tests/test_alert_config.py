"""Tests de los umbrales de avisos (05-reglas-alertas.md, «Umbrales configurables»)."""

import pytest

from servio_vision.alerts.config import AlertConfig, load_config


def test_production_defaults():
    config = load_config(interval=30, demo=False, env={})
    assert (config.t1, config.t2, config.reminder) == (60, 600, 180)
    assert config.max_reminders == 2


@pytest.mark.parametrize("interval, expected", [(30, 60), (15, 30)])
def test_demo_defaults_are_two_intervals(interval, expected):
    config = load_config(interval=interval, demo=True, env={})
    assert config.t1 == config.t2 == config.reminder == expected


def test_flag_beats_env_and_env_beats_default():
    env = {"ALERT_T1": "90", "ALERT_T2": "120", "ALERT_REMINDER": ""}
    config = load_config(interval=30, demo=True, t1=75, env=env)
    assert config.t1 == 75  # flag
    assert config.t2 == 120  # variable de entorno
    assert config.reminder == 60  # vacía: valor de demo


def test_reads_os_environ_by_default(monkeypatch):
    monkeypatch.setenv("ALERT_T2", "300")
    assert load_config(interval=30, demo=False).t2 == 300


def test_rejects_threshold_below_two_intervals():
    with pytest.raises(ValueError, match="t1=45s"):
        load_config(interval=30, demo=False, t1=45, env={})


def test_production_with_long_interval_does_not_start():
    # T1 = 60 s no se puede confirmar si el pipeline lee cada 40 s.
    with pytest.raises(ValueError, match="80s"):
        load_config(interval=40, demo=False, env={})


def test_rejects_non_integer_env():
    with pytest.raises(ValueError, match="ALERT_T1"):
        load_config(interval=30, demo=False, env={"ALERT_T1": "1m"})


def test_rejects_non_positive_interval():
    with pytest.raises(ValueError, match="interval"):
        AlertConfig(interval=0, t1=60, t2=60, reminder=60)


def test_config_is_immutable():
    config = load_config(interval=30, demo=True, env={})
    with pytest.raises(AttributeError):
        config.t1 = 1
