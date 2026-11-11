"""Un reinicio de Servio no pierde los tiempos de los platos ni los avisos abiertos."""

from datetime import UTC, datetime

import pytest

from servio_vision import alerts
from servio_vision.alerts.processor import AlertProcessor
from servio_vision.db import Database
from servio_vision.restaurant import Restaurant
from tests.test_alert_engine import obs
from tests.test_alert_process import (
    COMIENDO,
    DEMO,
    LLENO,
    VACIO,
    RecordingNotifier,
)


@pytest.fixture(autouse=True)
def no_global_processor(monkeypatch):
    monkeypatch.setattr(alerts, "_processor", None)


def start(tmp_path, *, resume: bool, restaurant=None, **kwargs) -> AlertProcessor:
    """Un «arranque» de Servio sobre la misma base y el mismo log."""
    return AlertProcessor(
        DEMO,
        RecordingNotifier(),
        tmp_path / "eventos.jsonl",
        restaurant=restaurant,
        db=Database(tmp_path / "servio.db"),
        resume=resume,
        **kwargs,
    )


def test_plate_timers_survive_a_restart(tmp_path):
    first = start(tmp_path, resume=False)
    first.process(obs(0, VACIO, LLENO))
    first.process(obs(30, VACIO, LLENO))
    first.close()

    second = start(tmp_path, resume=True)
    try:
        second.process(obs(60, VACIO, LLENO))  # 60 s desde el primer frame
        assert len(second.notifier.sent) == 1  # abrió el aviso, sin esperar de nuevo
    finally:
        second.close()


def test_without_resume_the_old_state_is_ignored(tmp_path):
    """Un video o un fixture no tienen que seguir lo que dejó la corrida anterior."""
    first = start(tmp_path, resume=False)
    first.process(obs(0, VACIO, LLENO))
    first.process(obs(30, VACIO, LLENO))
    first.close()

    second = start(tmp_path, resume=False)
    try:
        second.process(obs(60, VACIO, LLENO))
        assert second.notifier.sent == []  # los platos cuentan desde 60
    finally:
        second.close()


def test_open_alert_is_not_repeated_and_its_message_is_still_edited(tmp_path):
    first = start(tmp_path, resume=False)
    for s in (0, 30, 60):
        first.process(obs(s, VACIO, LLENO))
    (message_id,) = {a.telegram_message_id for a in first.open_alerts.values()}
    opened = set(first.open_alerts)
    first.close()

    second = start(tmp_path, resume=True)
    try:
        assert set(second.open_alerts) == opened
        second.process(obs(90, VACIO, LLENO))
        assert second.notifier.sent == []  # el mismo problema no se avisa de nuevo
        second.process(obs(120, LLENO))  # retiran el plato vacío
        assert [m for m, _ in second.notifier.edits] == [message_id]
    finally:
        second.close()


def test_stale_state_is_discarded(tmp_path):
    first = start(tmp_path, resume=False)
    for s in (0, 30, 60):
        first.process(obs(s, VACIO, LLENO))
    first.close()

    second = start(tmp_path, resume=True, resume_max_age_s=-1)  # todo es viejo
    try:
        assert second.tables == {}
        assert second.open_alerts == {}
    finally:
        second.close()


def test_served_plates_are_not_inserted_again_after_a_restart(tmp_path):
    restaurant = Restaurant.load()
    first = start(tmp_path, resume=False, restaurant=restaurant)
    first.process(obs(0, VACIO, COMIENDO))
    first.close()

    second = start(tmp_path, resume=True, restaurant=Restaurant.load())
    try:
        second.process(obs(30, VACIO, COMIENDO))
        (row,) = second.db.query("SELECT count(*) AS n FROM platos_servidos")
        assert row["n"] == 2
    finally:
        second.close()


def test_configure_passes_resume_to_the_processor(tmp_path):
    log = tmp_path / "eventos.jsonl"
    first = alerts.configure(
        DEMO, telegram=False, log_path=log, restaurant=Restaurant.load(), env={}
    )
    first.process(obs(0, VACIO, COMIENDO))
    resumed = alerts.configure(
        DEMO,
        telegram=False,
        log_path=log,
        restaurant=Restaurant.load(),
        env={},
        resume=True,
    )
    try:
        assert set(resumed.tables) == {"T1"}
    finally:
        resumed.close()


@pytest.mark.parametrize(
    ("source", "live"),
    [
        ("0", True),
        ("rtsp://192.168.0.5/stream", True),
        ("http://192.168.0.10:4747/video", True),
        ("salon.mp4", False),
        ("fotos/", False),
    ],
)
def test_only_cameras_are_live_sources(source, live):
    from servio_vision.capture import is_live_source

    assert is_live_source(source) is live


def test_database_keeps_one_state_per_local(tmp_path):
    path = tmp_path / "servio.db"
    a, b = Database(path, local_id="casa-a"), Database(path, local_id="casa-b")
    try:
        assert a.load_state() is None
        at = datetime(2026, 10, 29, 20, 0, tzinfo=UTC)
        a.save_state('{"x": 1}', at)
        b.save_state('{"x": 2}', at)
        a.save_state('{"x": 3}', at)  # pisa el anterior de la casa-a
        assert a.load_state() == ('{"x": 3}', at)
        assert b.load_state() == ('{"x": 2}', at)
    finally:
        a.close()
        b.close()
