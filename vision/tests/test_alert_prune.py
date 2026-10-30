"""El estado de `alerts` que se guarda para un reinicio no crece con cada plato servido."""

import json

import pytest

from servio_vision import alerts
from servio_vision.alerts.processor import PRUNE_SERVED_AFTER_READINGS, AlertProcessor
from servio_vision.restaurant import Restaurant
from tests.test_alert_db_failures import RecordingDb, recording
from tests.test_alert_engine import obs
from tests.test_alert_process import DEMO, RecordingNotifier


@pytest.fixture(autouse=True)
def no_global_processor(monkeypatch):
    monkeypatch.setattr(alerts, "_processor", None)


def plate(n: int, state: str = "comiendo") -> tuple[str, str, str]:
    return (f"T1-p{n}", state, "centro")


class SnapshotDb(RecordingDb):
    """Anota la última foto de estado que se le guardó y los avisos que se abrieron."""

    def __init__(self) -> None:
        super().__init__()
        self.last_state = ""
        self.opened: list[tuple] = []

    def save_state(self, state, at) -> None:
        self.last_state = state
        super().save_state(state, at)

    def alert_opened(self, *args) -> None:
        self.opened.append(args)


def test_a_plate_that_left_is_forgotten_after_the_grace_period(tmp_path):
    p = recording(tmp_path, RecordingDb(), Restaurant.load())
    p.process(obs(0, plate(1), plate(2)))
    seconds = 0
    for _ in range(PRUNE_SERVED_AFTER_READINGS - 1):
        seconds += 30
        p.process(obs(seconds, plate(1)))  # p2 ya no está
    assert "T1-p2" in p._served_rows  # todavía dentro del plazo
    seconds += 30
    p.process(obs(seconds, plate(1)))
    assert set(p._served_rows) == {"T1-p1"}
    p.close()


def test_a_plate_that_comes_back_in_time_is_not_inserted_twice(tmp_path):
    """Un id puede volver unas lecturas después (lo taparon): sigue siendo el mismo
    plato servido, no una fila nueva en las métricas."""
    db = RecordingDb()
    p = recording(tmp_path, db, Restaurant.load())
    p.process(obs(0, plate(1), plate(2)))
    for k in range(1, 6):
        p.process(obs(30 * k, plate(1)))  # p2 se va cinco lecturas
    p.process(obs(180, plate(1), plate(2)))  # y vuelve
    assert db._next_id == 2  # `served` se llamó una vez por plato
    p.close()


def test_saved_state_does_not_grow_with_every_plate_served(tmp_path):
    db = SnapshotDb()
    p = recording(tmp_path, db, Restaurant.load())
    seconds = 0
    for n in range(1, 151):  # 150 platos, cada uno tres lecturas y se va
        for _ in range(3):
            p.process(obs(seconds, plate(n)))
            seconds += 30
    p.close()
    saved = json.loads(db.last_state)["served_rows"]
    # lo que cabe en el plazo (30 lecturas / 3 por plato) y el plato de ahora, no 150
    assert len(p._served_rows) <= PRUNE_SERVED_AFTER_READINGS // 3 + 2
    assert len(saved) == len(p._served_rows)


def test_the_plate_of_an_alert_is_forgotten_once_the_alert_is_recorded(tmp_path):
    class ButtonNotifier(RecordingNotifier):
        def send(self, text, buttons=None):  # el aviso de plato lleno lleva botones
            return super().send(text)

    db = SnapshotDb()
    p = AlertProcessor(
        DEMO,
        ButtonNotifier(),
        tmp_path / "eventos.jsonl",
        restaurant=Restaurant.load(),
        db=db,  # type: ignore[arg-type]
    )
    for seconds in (0, 30, 60):
        p.process(obs(seconds, plate(1, "lleno")))
    assert [a.type for a in p.open_alerts.values()] == ["plato_lleno_mucho"]
    assert p._alert_plate == {}  # ya se usó: nadie lo vuelve a leer
    (opened,) = db.opened
    assert opened[3] == p._served_rows["T1-p1"]  # y el aviso quedó ligado a su plato
    p.close()
