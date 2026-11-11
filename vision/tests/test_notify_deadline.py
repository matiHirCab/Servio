"""Telegram no frena el loop: un tope de tiempo por envío y, si no llega, una cola."""

import json
import logging
import time

import httpx
import pytest

from servio_vision.alerts import notify
from servio_vision.alerts.notify import TelegramNotifier

TOKEN = "123:secreto"
CHAT_ID = "-1001"


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class Net:
    """Telegram y la red: `up` dice si contesta."""

    def __init__(self) -> None:
        self.up = False
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if not self.up:
            raise httpx.ConnectError("sin red", request=request)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 7}})

    def texts(self) -> list[str]:
        return [json.loads(r.content).get("text") for r in self.requests]


def build(handler=None, **kwargs):
    clock = Clock()
    net = handler or Net()
    sleeps: list[float] = []

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock.now += seconds

    kwargs.setdefault("background", False)
    tg = TelegramNotifier(
        TOKEN,
        CHAT_ID,
        client=httpx.Client(transport=httpx.MockTransport(net)),
        sleep=sleep,
        clock=clock,
        **kwargs,
    )
    return tg, net, clock, sleeps


def test_a_send_never_waits_more_than_the_deadline():
    clock = Clock()
    timeouts: list[float] = []

    def slow(request: httpx.Request) -> httpx.Response:
        read = request.extensions["timeout"]["read"]
        timeouts.append(read)
        clock.now += min(read, 6)  # un timeout de verdad corta en el tope que se le dio
        raise httpx.ReadTimeout("lento", request=request)

    tg = TelegramNotifier(
        TOKEN,
        CHAT_ID,
        client=httpx.Client(transport=httpx.MockTransport(slow)),
        sleep=lambda s: setattr(clock, "now", clock.now + s),
        clock=clock,
        background=False,
        deadline_s=8.0,
    )
    start = clock.now
    assert tg.send("hola") is None
    assert clock.now - start <= 8.0 + 1e-6
    # cada intento se corta en lo que queda del plazo, no en los 10 s de siempre
    assert timeouts == pytest.approx([8.0, 1.0])


def test_a_long_retry_after_does_not_wait_past_the_deadline():
    def throttled(request):
        return httpx.Response(
            429, json={"ok": False, "parameters": {"retry_after": 10}}
        )

    tg, _, _, sleeps = build(throttled)
    assert tg.send("hola") is None
    assert sleeps == []  # esperar 10 s con 8 s de plazo no tiene sentido
    assert tg.pending == 1  # va a la cola


def test_a_failed_send_is_queued_and_delivered_later():
    tg, net, _, _ = build()
    assert tg.send("hola") is None
    assert tg.pending == 1
    net.up = True
    assert tg.retry_backlog() == 0
    assert net.texts()[-1] == "hola"


def test_sends_queue_behind_the_backlog_without_touching_the_network():
    tg, net, _, _ = build()
    tg.send("a")
    calls = len(net.requests)
    tg.send("b")  # hay cola: no se vuelve a esperar a una red que no anda
    assert len(net.requests) == calls
    assert tg.pending == 2
    net.up = True
    assert tg.retry_backlog() == 0
    assert net.texts()[-2:] == ["a", "b"]  # en orden


def test_retry_stops_at_the_first_failure_while_the_network_is_down():
    tg, net, _, _ = build()
    tg.send("a")
    tg.send("b")
    calls = len(net.requests)
    assert tg.retry_backlog() == 2
    assert len(net.requests) == calls + 1  # un intento y frena: sigue sin red


def test_old_messages_are_dropped(caplog):
    tg, net, clock, _ = build()
    tg.send("a")
    clock.now += notify.BACKLOG_MAX_AGE_S + 1
    net.up = True
    calls = len(net.requests)
    with caplog.at_level(logging.ERROR):
        assert tg.retry_backlog() == 0
    assert len(net.requests) == calls  # no se manda un aviso viejo
    assert "viejo" in caplog.text


def test_a_client_error_is_not_queued():
    tg, _, _, _ = build(
        lambda r: httpx.Response(400, json={"ok": False, "description": "mal"})
    )
    assert tg.send("hola") is None
    assert tg.pending == 0


def test_an_edit_is_queued_too():
    tg, net, _, _ = build()
    assert tg.edit(5, "listo") is False
    assert tg.pending == 1
    net.up = True
    tg.retry_backlog()
    last = net.requests[-1]
    assert last.url.path.endswith("/editMessageText")
    assert json.loads(last.content)["message_id"] == 5


def test_the_backlog_is_bounded(monkeypatch, caplog):
    monkeypatch.setattr(notify, "BACKLOG_MAX", 3)
    tg, net, _, _ = build()
    with caplog.at_level(logging.ERROR):
        for i in range(5):
            tg.send(f"m{i}")
    assert tg.pending == 3
    net.up = True
    tg.retry_backlog()
    assert net.texts()[-3:] == ["m2", "m3", "m4"]  # se perdieron los más viejos
    assert "descarto" in caplog.text


def test_the_background_worker_delivers_when_telegram_comes_back(monkeypatch):
    monkeypatch.setattr(notify, "RETRY_WAIT_S", 0.01)
    monkeypatch.setattr(notify, "BACKLOG_RETRY_MAX_S", 0.02)
    net = Net()
    tg = TelegramNotifier(
        TOKEN,
        CHAT_ID,
        client=httpx.Client(transport=httpx.MockTransport(net)),
        deadline_s=1.0,
    )
    try:
        assert tg.send("hola") is None
        net.up = True
        deadline = time.monotonic() + 3
        while tg.pending and time.monotonic() < deadline:
            time.sleep(0.01)
        assert tg.pending == 0
        assert net.texts()[-1] == "hola"
    finally:
        tg.close()
    assert not tg.worker_running


def test_close_says_what_was_lost(caplog):
    tg, _, _, _ = build()
    tg.send("a")
    with caplog.at_level(logging.WARNING):
        tg.close()
    assert "sin entregar" in caplog.text
