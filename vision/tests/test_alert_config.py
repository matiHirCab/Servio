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


# --- C-5: recordatorios antes de escalar y los 10 min de «Listo» ---


def test_escalation_and_done_wait_defaults():
    prod = load_config(interval=30, demo=False, env={})
    assert (prod.max_reminders, prod.done_wait) == (2, 600)
    demo = load_config(interval=30, demo=True, env={})
    # en demo los tiempos son 2 × interval, la cantidad de recordatorios no cambia
    assert (demo.max_reminders, demo.done_wait) == (2, 60)


def test_escalation_and_done_wait_from_env_and_flags():
    env = {"ALERT_MAX_REMINDERS": "1", "ALERT_DONE_WAIT": "300"}
    config = load_config(interval=30, demo=False, env=env)
    assert (config.max_reminders, config.done_wait) == (1, 300)
    config = load_config(interval=30, demo=False, max_reminders=0, env=env)
    assert config.max_reminders == 0  # 0 vale: escala en el primer R


def test_reads_escalation_settings_by_default(monkeypatch):
    monkeypatch.setenv("ALERT_MAX_REMINDERS", "3")
    monkeypatch.setenv("ALERT_DONE_WAIT", "240")
    config = load_config(interval=30, demo=False)
    assert (config.max_reminders, config.done_wait) == (3, 240)


def test_rejects_a_done_wait_below_two_intervals():
    with pytest.raises(ValueError, match="done_wait=45s"):
        load_config(interval=30, demo=False, done_wait=45, env={})


def test_rejects_negative_max_reminders():
    with pytest.raises(ValueError, match="max_reminders"):
        AlertConfig(interval=30, t1=60, t2=600, reminder=180, max_reminders=-1)


def test_an_old_run_config_without_the_new_fields_still_loads():
    # replay.py reconstruye la config de un log de antes de C-5
    old = {"interval": 30, "t1": 60, "t2": 600, "reminder": 180, "max_reminders": 2}
    assert AlertConfig(**old).done_wait == 600
