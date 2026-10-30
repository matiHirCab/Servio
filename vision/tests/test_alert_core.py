"""El núcleo de los avisos (A-4) solo, con fixtures: sin log, sin base, sin notificador."""

import json

import pytest

from servio_vision.alerts.core import (
    AlertCore,
    AlertsOpened,
    AlertsReminded,
    AlertsResolved,
    TableUpdated,
)
from servio_vision.restaurant import Restaurant
from tests.test_alert_engine import FIXTURES, PROD, SCENARIOS, load, obs

NAMES = {
    AlertsOpened: "alert_opened",
    AlertsReminded: "alert_reminder",
    AlertsResolved: "alert_resolved",
}


@pytest.mark.parametrize("name", SCENARIOS)
def test_core_alone_gives_the_expected_alerts(tmp_path, monkeypatch, name):
    monkeypatch.chdir(tmp_path)  # si el núcleo escribiera algo, quedaría acá
    core = AlertCore(PROD)
    observations = load(name)
    start = observations[0].ts
    got = []
    for o in observations:
        events = core.apply(o)
        assert isinstance(events[0], TableUpdated)
        for event in events[1:]:
            for alert in event.alerts:
                got.append(
                    {
                        "at_seconds": int((o.ts - start).total_seconds()),
                        "event": NAMES[type(event)],
                        "type": alert.type,
                        "table_id": alert.table_id,
                    }
                )
    expected = json.loads((FIXTURES / "expected" / f"{name}.alerts.json").read_text())
    assert got == expected
    assert list(tmp_path.iterdir()) == []


def test_alerts_of_one_table_open_in_one_event():
    core = AlertCore(PROD)
    vacio, lleno = ("T1-p1", "vacio", "centro"), ("T1-p2", "lleno", "abajo")
    opened = []
    for s in range(0, 1200, 30):
        opened += [
            e for e in core.apply(obs(s, vacio, lleno)) if type(e) is AlertsOpened
        ]
    assert [len(e.alerts) for e in opened] == [1, 1]  # plato_vacio y, más tarde, lleno
    assert opened[0].text == "🧹 Mesa 1: hay platos vacíos para retirar."


def test_with_restaurant_the_full_plate_alert_asks_alone():
    core = AlertCore(PROD, Restaurant.load())
    lleno = ("T1-p1", "lleno", "centro")
    events = [e for s in range(0, 1200, 30) for e in core.apply(obs(s, lleno))]
    (opened,) = [e for e in events if type(e) is AlertsOpened]
    assert opened.ask is not None and opened.ask.plate == "T1-p1"
    assert opened.text.startswith("❓ Juan · Mesa 1: Cookies con chips de chocolate")


def test_closing_a_finished_table_says_why():
    core = AlertCore(PROD)
    vacio = ("T1-p1", "vacio", "centro")
    for s in range(0, 300, 30):
        core.apply(obs(s, vacio))
    (resolved,) = [e for e in core.apply(obs(300)) if type(e) is AlertsResolved]
    (alert,) = resolved.alerts
    assert resolved.reasons == {alert.id: "retirados"}
    assert core.open_alerts == {}


def test_attach_keeps_the_message_id_of_an_open_alert():
    core = AlertCore(PROD)
    vacio = ("T1-p1", "vacio", "centro")
    opened = None
    for s in range(0, 300, 30):
        for e in core.apply(obs(s, vacio)):
            if type(e) is AlertsOpened:
                opened = e
    assert opened is not None
    core.attach(
        tuple(a.model_copy(update={"telegram_message_id": 7}) for a in opened.alerts)
    )
    assert {a.telegram_message_id for a in core.open_alerts.values()} == {7}


def test_rules_changed_ignores_a_reading_equal_to_the_last():
    core = AlertCore(PROD)
    vacio = ("T1-p1", "vacio", "centro")
    assert core.apply(obs(0, vacio))[0].rules_changed  # plato nuevo
    assert not core.apply(obs(30, vacio))[0].rules_changed
    assert core.apply(obs(60, ("T1-p1", "lleno", "centro")))[0].rules_changed
