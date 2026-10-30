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


def test_a_rules_change_is_saved_at_most_once_per_second(tmp_path):
    """Cambia el estado de un plato en cada lectura (sin avisos): se guarda la
    primera, después espera `SAVE_RULES_EVERY_S`, y lo que quedó pendiente se
    guarda en la próxima lectura pasado ese tiempo."""
    from servio_vision.alerts.processor import SAVE_RULES_EVERY_S

    db = RecordingDb()
    p = recording(tmp_path, db)
    now = [100.0]
    p._clock = lambda: now[0]
    lleno, comiendo = (
        ("T1-p9", "lleno", "izquierda"),
        ("T1-p9", "comiendo", "izquierda"),
    )
    p.process(obs(0, lleno))  # plato nuevo: se guarda
    assert db.saves == 1
    now[0] += SAVE_RULES_EVERY_S / 4
    p.process(obs(5, comiendo))  # cambio de reglas, pero hace menos de 1 s
    now[0] += SAVE_RULES_EVERY_S / 4
    p.process(obs(10, comiendo))  # nada nuevo
    assert db.saves == 1
    now[0] += SAVE_RULES_EVERY_S
    p.process(obs(15, comiendo))  # nada nuevo, pero quedó un cambio sin guardar
    assert db.saves == 2
    now[0] += SAVE_RULES_EVERY_S
    p.process(obs(20, comiendo))  # nada pendiente: no se guarda
    assert db.saves == 2
    p.close()


def test_with_the_db_down_a_reading_does_not_force_a_save_every_time(tmp_path):
    """Un plato que no se pudo insertar no fuerza un guardado en cada lectura (con
    la base caída cada intento es otra espera de conexión): sin cambios de
    reglas, se reintenta con el guardado de cada `SAVE_STATE_EVERY_S`."""
    from servio_vision.alerts.processor import SAVE_STATE_EVERY_S

    class CountingBrokenDb(BrokenDb):
        def __init__(self) -> None:
            self.save_attempts = 0

        def save_state(self, state, at) -> None:
            self.save_attempts += 1
            raise RuntimeError("base caída")

    db = CountingBrokenDb()
    p = make(tmp_path, Restaurant.load())
    p.db = p.db_output.db = db  # type: ignore[assignment,union-attr]
    now = [100.0]
    p._clock = lambda: now[0]
    plate = ("T1-p9", "comiendo", "izquierda")
    p.process(obs(0, plate))  # plato nuevo: intenta guardar y falla
    now[0] += 0.1
    p.process(obs(5, plate))  # deja de ser reciente: cambio de reglas, < 1 s
    for s in range(10, 30, 5):  # lecturas iguales, cada una 1 s después
        now[0] += 1
        p.process(obs(s, plate))
    # el primero y un reintento al segundo (por si fue un parpadeo); con el segundo
    # fallo la base está caída: no uno por lectura
    assert db.save_attempts == 2
    now[0] += SAVE_STATE_EVERY_S
    p.process(obs(40, plate))
    assert db.save_attempts == 3  # el periódico
    p.close()


def test_a_failed_save_of_a_rules_change_is_retried_after_a_second(tmp_path):
    """La base parpadea justo al guardar un cambio de reglas: sigue pendiente y se
    reintenta pasado `SAVE_RULES_EVERY_S`, no recién en el guardado de cada 5 s."""
    from servio_vision.alerts.processor import SAVE_RULES_EVERY_S

    class FlakyDb(RecordingDb):
        def __init__(self) -> None:
            super().__init__()
            self.fail_saves = 0

        def save_state(self, state, at) -> None:
            if self.fail_saves:
                self.fail_saves -= 1
                raise RuntimeError("base caída")
            super().save_state(state, at)

    db = FlakyDb()
    p = recording(tmp_path, db)
    now = [100.0]
    p._clock = lambda: now[0]
    plate = ("T1-p9", "lleno", "izquierda")
    p.process(obs(0, plate))
    assert db.saves == 1
    db.fail_saves = 1
    now[0] += SAVE_RULES_EVERY_S
    p.process(obs(5, ("T1-p9", "comiendo", "izquierda")))  # el guardado falla
    assert db.saves == 1
    now[0] += SAVE_RULES_EVERY_S
    p.process(obs(10, ("T1-p9", "comiendo", "izquierda")))  # nada nuevo: reintenta
    assert db.saves == 2
    p.close()


def test_a_waiters_answer_reaches_the_db_even_if_the_log_fails(tmp_path):
    db = RecordingDb()
    answers = []
    db.feedback = answers.append  # type: ignore[method-assign]
    p = recording(tmp_path, db)

    def broken(event):
        raise OSError("disco lleno")

    p.log.write = broken  # type: ignore[method-assign]
    p._on_feedback({"event": "alert_feedback", "answer": "No le gustó"})
    assert answers == [{"event": "alert_feedback", "answer": "No le gustó"}]
    p.close()


def test_state_is_saved_right_away_when_an_alert_opens(tmp_path):
    db = RecordingDb()
    p = recording(tmp_path, db)
    p.process(obs(0, VACIO, COMIENDO))
    before = db.saves
    p.process(obs(60, VACIO, COMIENDO))  # abre plato_vacio
    assert p.open_alerts and db.saves == before + 1
    p.close()


def test_state_is_saved_right_away_when_a_plate_is_served(tmp_path):
    """El id de la fila de un plato servido tiene que llegar al estado guardado: si
    no, tras un reinicio se inserta el plato de nuevo."""
    db = RecordingDb()
    p = recording(tmp_path, db, Restaurant.load())
    p.process(obs(0, COMIENDO))
    before = db.saves
    p.process(obs(10, COMIENDO, VACIO))  # plato nuevo, sin aviso y sin pasar 5 s
    assert db.saves == before + 1
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
                p.db_output.write("el estado", boom)
            p.db_output.write(
                "el aviso abierto", boom
            )  # otra escritura: se loguea aparte
        messages = [r.getMessage() for r in caplog.records]
        assert len([m for m in messages if "el estado" in m]) == 1
        assert len([m for m in messages if "el aviso abierto" in m]) == 1

        # otro error en la misma escritura no se calla: es otra causa
        def schema_error() -> None:
            raise ValueError("no existe la columna")

        with caplog.at_level(logging.ERROR):
            p.db_output.write("el estado", schema_error)
        assert "No se pudo guardar el estado" in caplog.records[-1].getMessage()
        assert caplog.records[-1].exc_info[0] is ValueError

        # pasado el minuto vuelve a loguear y cuenta los que se callaron
        p.db_output._logged[("el estado", "RuntimeError")] -= 61
        with caplog.at_level(logging.ERROR):
            p.db_output.write("el estado", boom)
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
