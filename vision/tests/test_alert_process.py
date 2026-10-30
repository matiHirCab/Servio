"""Tests de `alerts.process()` (MAT-5): estado + motor + Telegram + log, sin red."""

import json
from datetime import datetime
from pathlib import Path

import pytest

from servio_vision import alerts
from servio_vision.alerts.config import AlertConfig, load_config
from servio_vision.alerts.models import TableObservation
from servio_vision.alerts.notify import resolved_text
from servio_vision.alerts.processor import AlertProcessor
from tests.test_alert_engine import FIXTURES, PROD, SCENARIOS, load, obs

DEMO = load_config(interval=30, demo=True, env={})  # T1 = T2 = R = 60 s


class RecordingNotifier:
    """Guarda lo que se manda y edita; `fail=True` simula Telegram caído."""

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.sent: list[str] = []
        self.edits: list[tuple[int, str]] = []

    def send(self, text: str) -> int | None:
        self.sent.append(text)
        return None if self.fail else len(self.sent)

    def edit(self, message_id: int, text: str) -> bool:
        self.edits.append((message_id, text))
        return not self.fail


_created: list[AlertProcessor] = []


@pytest.fixture(autouse=True)
def close_logs(monkeypatch):
    # Cada test arranca sin el procesador global de alerts y cierra los logs que abrió.
    monkeypatch.setattr(alerts, "_processor", None)
    yield
    for p in [*_created, alerts._processor]:
        if p is not None:
            p.close()
    _created.clear()


def processor(tmp_path: Path, config=PROD, fail: bool = False) -> AlertProcessor:
    p = AlertProcessor(config, RecordingNotifier(fail), tmp_path / "eventos.jsonl")
    _created.append(p)
    return p


def log_lines(p: AlertProcessor) -> list[dict]:
    """Las líneas del log después de `run_started`."""
    lines = [json.loads(line) for line in p.log.path.read_text("utf-8").splitlines()]
    assert lines[0]["event"] == "run_started"
    return lines[1:]


def test_log_starts_with_run_config(tmp_path):
    p = processor(tmp_path, DEMO)
    (line,) = [json.loads(x) for x in p.log.path.read_text("utf-8").splitlines()]
    assert line["event"] == "run_started"
    assert AlertConfig(**line["config"]) == DEMO
    assert datetime.fromisoformat(line["started_at"]).tzinfo is not None


VACIO = ("T1-p1", "vacio", "centro")
# otro plato que se sigue comiendo: con él, un plato vacío es plato_vacio (sin él, la
# mesa entera terminó y avisa mesa_terminada)
COMIENDO = ("T1-p9", "comiendo", "izquierda")
LLENO = ("T1-p2", "lleno", "abajo-derecha")


@pytest.mark.parametrize("name", SCENARIOS)
def test_fixture_log_matches_expected(tmp_path, name):
    p = processor(tmp_path)
    observations = load(name)
    for o in observations:
        p.process(o)
    start = observations[0].ts
    alert_events = [
        {
            "at_seconds": int(
                (datetime.fromisoformat(e["ts"]) - start).total_seconds()
            ),
            "event": e["event"],
            "type": e["type"],
            "table_id": e["table_id"],
        }
        for e in log_lines(p)
        if e["event"] != "observation"
    ]
    expected = json.loads((FIXTURES / "expected" / f"{name}.alerts.json").read_text())
    assert alert_events == expected
    assert sum(e["event"] == "observation" for e in log_lines(p)) == len(observations)


def test_unattended_table_reminds_twice_with_growing_time_and_closes_with_a_note(
    tmp_path,
):
    """`mesa_terminada_recordatorios`: más allá del tipo y el segundo (lo que compara
    `fixtures/expected`), los textos, el conteo de recordatorios (máximo 2) y el cierre."""
    p = processor(tmp_path)
    for o in load("mesa_terminada_recordatorios"):
        p.process(o)
    alert_events = [e for e in log_lines(p) if e["event"] != "observation"]
    assert [(e["event"], e["reminders_sent"]) for e in alert_events] == [
        ("alert_opened", 0),
        ("alert_reminder", 1),
        ("alert_reminder", 2),
        ("alert_resolved", 2),  # un tercer recordatorio a los 720 s no sale
    ]
    texts = [e["text"] for e in alert_events]
    assert texts[0] == "🍽️ Mesa 4: terminó."
    assert texts[1] == "🍽️ Mesa 4: terminó hace 3 min."
    assert texts[2] == "🍽️ Mesa 4: terminó hace 6 min."
    # al cerrar: quién retiró y cuánto después de que terminó la mesa (750 − 180 s)
    assert "Retiraron los platos 9 min 30 s después de que terminó la mesa." in texts[3]
    assert alert_events[3]["close_reason"] == "retirados"


def test_no_close_note_is_kept_for_an_alert_without_a_telegram_message(tmp_path):
    """Si Telegram falló al abrir el aviso no hay mensaje que editar: la línea de
    «retiraron los platos» no tiene a quién llegar y no se guarda."""
    p = processor(tmp_path, DEMO, fail=True)
    for s in (0, 30, 60):
        p.process(obs(s, VACIO))  # solo platos vacíos: mesa_terminada
    assert [a.type for a in p.open_alerts.values()] == ["mesa_terminada"]
    p.process(obs(90))  # retiran todo
    assert not p.open_alerts
    assert p._close_notes == {}


def test_closing_the_processor_closes_the_notifier(tmp_path):
    """El notificador de Telegram tiene un hilo con la cola de lo que no llegó."""

    class ClosableNotifier(RecordingNotifier):
        closed = False

        def close(self):
            self.closed = True

    p = AlertProcessor(PROD, ClosableNotifier(), tmp_path / "eventos.jsonl")
    p.close()
    assert p.notifier.closed


def test_observation_line_round_trips(tmp_path):
    p = processor(tmp_path)
    o = obs(0, VACIO, COMIENDO)
    p.process(o)
    (line,) = log_lines(p)
    assert line.pop("event") == "observation"
    assert TableObservation.model_validate_json(json.dumps(line)) == o


def test_alerts_of_one_table_go_in_one_message(tmp_path):
    p = processor(tmp_path, DEMO)
    for s in (0, 30, 60):
        p.process(obs(s, VACIO, LLENO))
    notifier = p.notifier
    # "por terminar" ya no avisa: a los 60 s se abren los dos, en un solo mensaje.
    grouped = (
        "🧹 Mesa 1: hay platos vacíos para retirar.\n"
        "❓ Mesa 1: un plato sigue lleno hace 1 min. ¿Está todo bien?"
    )
    assert notifier.sent == [grouped]
    ids = {a.type: a.telegram_message_id for a in p.open_alerts.values()}
    assert ids == {"plato_vacio": 1, "plato_lleno_mucho": 1}


def test_closing_part_of_a_group_marks_only_that_line(tmp_path):
    p = processor(tmp_path, DEMO)
    for s in (0, 30, 60):
        p.process(obs(s, VACIO, LLENO))
    # Retiran el plato vacío: se cierra plato_vacio, solo esa línea.
    p.process(obs(90, LLENO))
    edits = dict(p.notifier.edits)
    assert edits[1] == (
        "🧹 Mesa 1: hay platos vacíos para retirar. ✅\n"
        "❓ Mesa 1: un plato sigue lleno hace 1 min. ¿Está todo bien?"
    )
    # Se van sin comer: dos lecturas sin gente cierran el otro y el mensaje entero.
    p.process(obs(120, LLENO, people=False))
    p.process(obs(150, LLENO, people=False))
    assert p.notifier.edits[-1] == (1, resolved_text(edits[1].replace(" ✅", "")))
    assert p.open_alerts == {}


def test_reminder_sends_new_message_and_keeps_original_id(tmp_path):
    p = processor(tmp_path)
    for s in range(0, 241, 30):
        p.process(obs(s, VACIO, COMIENDO))
    assert p.notifier.sent == [
        "🧹 Mesa 1: hay platos vacíos para retirar.",
        "🧹 Mesa 1: los platos vacíos siguen sin retirar hace 4 min.",
    ]
    (alert,) = p.open_alerts.values()
    assert (alert.reminders_sent, alert.telegram_message_id) == (1, 1)
    reminder = log_lines(p)[-1]
    assert reminder["event"] == "alert_reminder"
    assert reminder["text"] == p.notifier.sent[1]


def test_telegram_down_keeps_alert_open_and_skips_edit(tmp_path):
    p = processor(tmp_path, fail=True)
    for s in (0, 30, 60):
        p.process(obs(s, VACIO, COMIENDO))
    (alert,) = p.open_alerts.values()
    assert alert.telegram_message_id is None
    p.process(obs(90))
    assert p.open_alerts == {}
    assert p.notifier.edits == []
    resolved = log_lines(p)[-1]
    assert (resolved["event"], resolved["text"]) == ("alert_resolved", None)
    assert p._resolved_ids == set()


def test_rejected_observation_is_still_logged(tmp_path):
    p = processor(tmp_path)
    p.process(obs(30, VACIO, COMIENDO))
    with pytest.raises(ValueError):
        p.process(obs(0, VACIO, COMIENDO))  # ts hacia atrás
    assert [line["ts"] for line in log_lines(p)] == [
        "2026-10-17T20:00:30Z",
        "2026-10-17T20:00:00Z",
    ]


def test_alert_log_line(tmp_path):
    p = processor(tmp_path)
    for s in (0, 30, 60):
        p.process(obs(s, VACIO, COMIENDO))
    line = log_lines(p)[-1]
    assert line == {
        "event": "alert_opened",
        "ts": "2026-10-17T20:01:00Z",
        "id": line["id"],
        "table_id": "T1",
        "type": "plato_vacio",
        "message": "🧹 Mesa 1: hay platos vacíos para retirar.",
        "created_at": "2026-10-17T20:01:00Z",
        "status": "abierta",
        "reminders_sent": 0,
        "telegram_message_id": 1,
        "text": "🧹 Mesa 1: hay platos vacíos para retirar.",
    }


def test_module_configure_and_process(tmp_path, capsys):
    log = tmp_path / "out" / "eventos.jsonl"
    alerts.configure(DEMO, telegram=False, log_path=log)
    for s in (0, 30, 60):
        alerts.process(obs(s, VACIO, COMIENDO))
    assert "[telegram #" in capsys.readouterr().out
    assert len(log.read_text("utf-8").splitlines()) == 5  # run + 3 obs + plato_vacio
