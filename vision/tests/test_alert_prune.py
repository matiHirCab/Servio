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


POSITIONS = ("arriba-izquierda", "abajo-derecha", "centro")


def plate(n: int, state: str = "comiendo") -> tuple[str, str, str]:
    # cada plato en su lugar: dos en el mismo lugar se toman por un cambio de id
    # (`alerts/state.py`), no por un plato que se va
    return (f"T1-p{n}", state, POSITIONS[(n - 1) % len(POSITIONS)])


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


def test_a_row_is_kept_until_the_plates_retirement_reached_the_database(tmp_path):
    """Con la base caída, el retiro de un plato no se pudo registrar: su fila no se
    olvida (sin ella, el retiro se perdería y las métricas lo seguirían contando en
    la mesa). Cuando la base vuelve, se registra y recién ahí se olvida."""
    outage = PRUNE_SERVED_AFTER_READINGS + 10
    db = RecordingDb()
    p = recording(tmp_path, db, Restaurant.load())
    p.process(
        obs(0, plate(1), plate(2))
    )  # la base anda: los dos platos quedan en la mesa
    db.fail_readings = outage  # y se cae
    for k in range(1, outage + 1):
        p.process(obs(30 * k, plate(1)))  # p2 se fue, pero el retiro no se registra
    assert "T1-p2" in p._served_rows  # más allá del plazo, pero sin registrar
    assert len(db.readings) == 1  # la base solo recibió la primera lectura

    seconds = 30 * (outage + 1)
    p.process(obs(seconds, plate(1)))  # la base vuelve
    p.process(obs(seconds + 30, plate(1)))
    retired = [gone for _, gone in db.readings if gone]
    assert retired == [[2]]  # el retiro de la fila 2 llegó, una sola vez
    assert "T1-p2" not in p._served_rows  # y ahora sí se olvida
    p.close()


def test_plates_are_matched_to_their_table_exactly(tmp_path):
    """Una mesa «mesa» no se lleva los platos de «mesa-1» por compartir el prefijo."""
    from datetime import UTC, datetime, timedelta

    from servio_vision.alerts.models import TableObservation

    def reading(table: str, seconds: int, plate_ids: list[str]) -> TableObservation:
        return TableObservation(
            schema_version=1,
            camera_id="cam-1",
            ts=datetime(2026, 10, 30, 20, 0, tzinfo=UTC) + timedelta(seconds=seconds),
            table_id=table,
            people_present=True,
            items=[
                {
                    "track_id": pid,
                    "state": "comiendo",
                    "fill_ratio": 0.55,
                    "confidence": 0.9,
                    "position": "centro",
                }
                for pid in plate_ids
            ],
        )

    p = recording(tmp_path, RecordingDb(), Restaurant.load())
    p.process(reading("mesa-1", 0, ["mesa-1-p1"]))
    for k in range(PRUNE_SERVED_AFTER_READINGS + 5):  # solo se lee la otra mesa
        p.process(reading("mesa", 30 * k, ["mesa-p1"]))
    assert "mesa-1-p1" in p._served_rows  # es de otra mesa: no se cuenta ausente
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
