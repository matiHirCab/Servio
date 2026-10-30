"""Un corte brusco a mitad de un fixture no cambia los avisos (A-5).

Se corta el proceso sin `close()` (como un corte de luz: no se guarda lo último),
se relanza con `resume=True` sobre la misma base y se sigue con el resto del
fixture. Los avisos, sus mensajes y sus ediciones tienen que ser los mismos que en
una corrida sin corte, corte donde se corte.

En producción un cambio de reglas se guarda a lo sumo una vez por segundo
(`SAVE_RULES_EVERY_S`): un corte puede perder ese último segundo, y eso se acepta.
Acá ese tope va en 0 y el guardado periódico se apaga: lo que se prueba es que los
guardados por evento (avisos, platos nuevos, cambios de reglas) alcanzan.
"""

import itertools
import json
import math
from pathlib import Path

import pytest

from servio_vision import alerts
from servio_vision.alerts import processor
from servio_vision.alerts.processor import AlertProcessor
from servio_vision.db import Database
from servio_vision.restaurant import Restaurant
from tests.test_alert_engine import PROD, SCENARIOS, load

ALERT_EVENTS = {"alert_opened", "alert_reminder", "alert_resolved"}


@pytest.fixture(autouse=True)
def no_global_processor(monkeypatch):
    monkeypatch.setattr(alerts, "_processor", None)


@pytest.fixture(autouse=True)
def only_event_saves(monkeypatch):
    monkeypatch.setattr(processor, "SAVE_RULES_EVERY_S", 0.0)
    monkeypatch.setattr(processor, "SAVE_STATE_EVERY_S", math.inf)


class Telegram:
    """Notificador falso. Los `message_id` siguen de un arranque al otro, como en
    Telegram: así la corrida cortada y la entera dan los mismos números."""

    def __init__(self, ids: itertools.count) -> None:
        self.ids = ids
        self.sent: list[tuple[int, str]] = []
        self.edits: list[tuple[int, str]] = []

    def send(self, text: str, buttons=None) -> int:
        message_id = next(self.ids)
        self.sent.append((message_id, text))
        return message_id

    def edit(self, message_id: int, text: str) -> bool:
        self.edits.append((message_id, text))
        return True


def start(directory: Path, ids, *, resume: bool) -> AlertProcessor:
    """Un arranque de Servio con los datos del restaurante: también guarda los
    platos servidos y pregunta con botones, lo que más estado tiene."""
    return AlertProcessor(
        PROD,
        Telegram(ids),
        directory / "eventos.jsonl",
        restaurant=Restaurant.load(),
        db=Database(directory / "servio.db"),
        resume=resume,
    )


def crash(p: AlertProcessor) -> None:
    """Se corta sin `close()`: no guarda el estado. Solo se sueltan los archivos
    (en Windows, abiertos no dejan borrar `tmp_path`)."""
    p.log.close()
    assert p.db is not None
    p.db.close()


def alert_lines(log: Path) -> list[dict]:
    lines = [json.loads(line) for line in log.read_text("utf-8").splitlines()]
    return [line for line in lines if line["event"] in ALERT_EVENTS]


def run(directory: Path, observations, cut: int | None):
    """Corre el fixture, con un corte antes de la lectura `cut`. Devuelve los
    avisos del log, lo mandado y lo editado."""
    directory.mkdir()
    ids = itertools.count(1)
    p = start(directory, ids, resume=False)
    sent, edits = p.notifier.sent, p.notifier.edits
    for n, o in enumerate(observations):
        if n == cut:
            crash(p)
            p = start(directory, ids, resume=True)
            p.notifier.sent, p.notifier.edits = sent, edits
        p.process(o)
    p.close()
    return alert_lines(directory / "eventos.jsonl"), sent, edits


@pytest.mark.parametrize("name", SCENARIOS)
def test_a_crash_mid_fixture_gives_the_same_alerts(tmp_path, name):
    """Corta antes de cada lectura, de a una: lo que se pierde en un corte depende
    de en cuál cae (sin guardar los cambios de las reglas, fallan solo algunas)."""
    observations = load(name)
    whole = run(tmp_path / "entera", observations, None)
    differ = [
        cut
        for cut in range(1, len(observations))
        if run(tmp_path / f"corte-{cut}", observations, cut) != whole
    ]
    assert differ == []


def test_a_stale_state_after_a_crash_starts_from_scratch(tmp_path):
    observations = load("mesa_completa")
    ids = itertools.count(1)
    first = start(tmp_path, ids, resume=False)
    for o in observations[:10]:
        first.process(o)
    assert first.tables
    crash(first)
    second = AlertProcessor(
        PROD,
        Telegram(ids),
        tmp_path / "eventos.jsonl",
        db=Database(tmp_path / "servio.db"),
        resume=True,
        resume_max_age_s=-1,  # todo lo guardado es viejo
    )
    try:
        assert second.tables == {} and second.open_alerts == {}
    finally:
        second.close()
