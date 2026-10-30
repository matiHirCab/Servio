"""La base es auxiliar: si falla, el aviso al mozo sale igual y el loop sigue."""

import logging

import pytest

from servio_vision import alerts
from servio_vision.alerts.processor import AlertProcessor
from servio_vision.restaurant import Restaurant
from tests.test_alert_engine import obs
from tests.test_alert_process import (
    COMIENDO,
    DEMO,
    LLENO,
    VACIO,
    RecordingNotifier,
)


class BrokenDb:
    """Base caída: cada escritura lanza (la carga inicial del restaurante no)."""

    def load_restaurant(self, restaurant) -> None:
        pass

    def close(self) -> None:
        pass

    def _down(self, *args, **kwargs):
        raise RuntimeError("base caída")

    served = plate_readings = alert_opened = alert_resolved = feedback = _down


@pytest.fixture(autouse=True)
def no_global_processor(monkeypatch):
    monkeypatch.setattr(alerts, "_processor", None)


def make(tmp_path, restaurant=None) -> AlertProcessor:
    p = AlertProcessor(
        DEMO,
        RecordingNotifier(),
        tmp_path / "eventos.jsonl",
        restaurant=restaurant,
        db=BrokenDb(),  # type: ignore[arg-type]
    )
    return p


def test_alert_is_sent_and_tracked_when_the_db_is_down(tmp_path, caplog):
    p = make(tmp_path)
    try:
        with caplog.at_level(logging.ERROR):
            for s in (0, 30, 60):
                p.process(obs(s, VACIO, LLENO))
        assert p.notifier.sent  # el mozo se enteró
        assert {a.type for a in p.open_alerts.values()} == {
            "plato_vacio",
            "plato_lleno_mucho",
        }
        assert "base" in caplog.text
    finally:
        p.close()


def test_alert_is_resolved_when_the_db_is_down(tmp_path):
    p = make(tmp_path)
    try:
        for s in (0, 30, 60):
            p.process(obs(s, VACIO, LLENO))
        p.process(obs(90, LLENO))  # retiran el plato vacío
        assert [a.type for a in p.open_alerts.values()] == ["plato_lleno_mucho"]
        assert p.notifier.edits  # el mensaje se editó igual
    finally:
        p.close()


def test_plate_readings_failure_does_not_block_the_alert(tmp_path):
    p = make(tmp_path, Restaurant.load())
    try:
        for s in (0, 30, 60):
            p.process(obs(s, VACIO, COMIENDO))
        assert p.notifier.sent
        assert [a.type for a in p.open_alerts.values()] == ["plato_vacio"]
    finally:
        p.close()
