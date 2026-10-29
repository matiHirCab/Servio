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


class RecordingDb:
    """Base que anota lo que le llega; `fail_readings` = cuántas veces falla
    `plate_readings` antes de andar."""

    def __init__(self, fail_readings: int = 0) -> None:
        self.fail_readings = fail_readings
        self.saves = 0
        self.readings: list[tuple[list, list]] = []
        self._next_id = 0

    def load_restaurant(self, restaurant) -> None:
        pass

    def close(self) -> None:
        pass

    def load_state(self):
        return None

    def save_state(self, state, at) -> None:
        self.saves += 1

    def served(self, *args) -> int:
        self._next_id += 1
        return self._next_id

    def plate_readings(self, readings, gone, at) -> None:
        if self.fail_readings:
            self.fail_readings -= 1
            raise RuntimeError("base caída")
        self.readings.append((readings, gone))

    def alert_opened(self, *args) -> None:
        pass

    def alert_resolved(self, *args) -> None:
        pass

    def feedback(self, event) -> None:
        pass


def recording(tmp_path, db, restaurant=None) -> AlertProcessor:
    return AlertProcessor(
        DEMO,
        RecordingNotifier(),
        tmp_path / "eventos.jsonl",
        restaurant=restaurant,
        db=db,  # type: ignore[arg-type]
    )


def test_state_is_not_saved_on_every_reading(tmp_path):
    db = RecordingDb()
    p = recording(tmp_path, db)
    for s in range(0, 50, 5):  # diez lecturas sin ningún aviso
        p.process(obs(s, VACIO, COMIENDO))
    assert db.saves == 1
    p.close()
    assert db.saves == 2  # al cerrar se guarda lo último


def test_state_is_saved_right_away_when_an_alert_opens(tmp_path):
    db = RecordingDb()
    p = recording(tmp_path, db)
    p.process(obs(0, VACIO, COMIENDO))
    before = db.saves
    p.process(obs(60, VACIO, COMIENDO))  # abre plato_vacio
    assert p.open_alerts and db.saves == before + 1
    p.close()


def test_failed_plate_readings_are_retried_with_the_same_changes(tmp_path):
    """Si la base falla, el estado que ya se creía guardado no se pierde: la próxima
    lectura vuelve a mandar los cambios de estado."""
    db = RecordingDb(fail_readings=1)
    p = recording(tmp_path, db, Restaurant.load())
    p.process(obs(0, VACIO, COMIENDO))  # plate_readings falla
    p.process(obs(30, VACIO, COMIENDO))
    ((readings, _),) = db.readings
    assert [changed for _, _, _, changed in readings] == [True, True]
    p.close()


def test_failed_readings_do_not_lose_the_plates_that_left(tmp_path):
    db = RecordingDb()
    p = recording(tmp_path, db, Restaurant.load())
    p.process(obs(0, VACIO, COMIENDO))
    db.fail_readings = 1
    p.process(obs(30, COMIENDO))  # se va el vacío, pero la base falla
    p.process(obs(60, COMIENDO))
    gone = [g for _, g in db.readings]
    assert any(g for g in gone), "el plato que se fue nunca se marcó como retirado"
    p.close()


def test_a_down_database_does_not_flood_the_log(tmp_path, caplog):
    p = make(tmp_path)

    def boom() -> None:
        raise RuntimeError("base caída")

    try:
        with caplog.at_level(logging.ERROR):
            for _ in range(20):  # veinte lecturas con la base caída
                p._db_write("el estado", boom)
            p._db_write("el aviso abierto", boom)  # otra escritura: se loguea aparte
        messages = [r.getMessage() for r in caplog.records]
        assert len([m for m in messages if "el estado" in m]) == 1
        assert len([m for m in messages if "el aviso abierto" in m]) == 1

        # pasado el minuto vuelve a loguear y cuenta los que se callaron
        p._db_logged["el estado"] -= 61
        with caplog.at_level(logging.ERROR):
            p._db_write("el estado", boom)
        assert "y 19 fallos más" in caplog.records[-1].getMessage()
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
