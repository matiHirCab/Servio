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


def test_observation_line_round_trips(tmp_path):
    p = processor(tmp_path)
    o = obs(0, VACIO)
    p.process(o)
    (line,) = log_lines(p)
    assert line.pop("event") == "observation"
    assert TableObservation.model_validate_json(json.dumps(line)) == o


def test_alerts_of_one_table_go_in_one_message(tmp_path):
    p = processor(tmp_path, DEMO)
    for s in (0, 30, 60):
        p.process(obs(s, VACIO, LLENO))
    notifier = p.notifier
    # A los 0 s la mesa queda por_terminar (1 de 2 vacío); a los 60 s, los otros dos.
    assert notifier.sent[0] == "🍽️ Mesa 1 está terminando."
    assert notifier.sent[1] == (
        "🧹 Mesa 1: hay platos vacíos para retirar.\n"
        "❓ Mesa 1: un plato sigue lleno hace 1 min. ¿Está todo bien?"
    )
    ids = {a.type: a.telegram_message_id for a in p.open_alerts.values()}
    assert ids == {"mesa_por_terminar": 1, "plato_vacio": 2, "plato_lleno_mucho": 2}


def test_closing_part_of_a_group_marks_only_that_line(tmp_path):
    p = processor(tmp_path, DEMO)
    for s in (0, 30, 60):
        p.process(obs(s, VACIO, LLENO))
    # Retiran el plato vacío: se cierran plato_vacio y mesa_por_terminar.
    p.process(obs(90, LLENO))
    edits = dict(p.notifier.edits)
    assert edits[1] == "🍽️ Mesa 1 está terminando.\n\n✅ ya resuelto"
    assert edits[2] == (
        "🧹 Mesa 1: hay platos vacíos para retirar. ✅\n"
        "❓ Mesa 1: un plato sigue lleno hace 1 min. ¿Está todo bien?"
    )
    # Se van sin comer: dos lecturas sin gente cierran el otro y el mensaje entero.
    p.process(obs(120, LLENO, people=False))
    p.process(obs(150, LLENO, people=False))
    assert p.notifier.edits[-1] == (2, resolved_text(edits[2].replace(" ✅", "")))
    assert p.open_alerts == {}


def test_reminder_sends_new_message_and_keeps_original_id(tmp_path):
    p = processor(tmp_path)
    for s in range(0, 241, 30):
        p.process(obs(s, VACIO))
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
        p.process(obs(s, VACIO))
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
    p.process(obs(30, VACIO))
    with pytest.raises(ValueError):
        p.process(obs(0, VACIO))  # ts hacia atrás
    assert [line["ts"] for line in log_lines(p)] == [
        "2026-10-17T20:00:30Z",
        "2026-10-17T20:00:00Z",
    ]


def test_alert_log_line(tmp_path):
    p = processor(tmp_path)
    for s in (0, 30, 60):
        p.process(obs(s, VACIO))
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
        alerts.process(obs(s, VACIO))
    assert "[telegram #" in capsys.readouterr().out
    assert len(log.read_text("utf-8").splitlines()) == 5  # run + 3 obs + plato_vacio
