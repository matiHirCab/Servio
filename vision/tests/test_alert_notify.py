"""Tests del envío a Telegram (MAT-4), sin red: httpx.MockTransport."""

import io
import json
import logging
import sys

import httpx
import pytest

from servio_vision.alerts.notify import (
    ConsoleNotifier,
    TelegramNotifier,
    get_notifier,
    resolved_text,
)

TOKEN = "123:secreto"
CHAT_ID = "-1001"


def notifier(handler) -> tuple[TelegramNotifier, list[httpx.Request]]:
    requests: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    client = httpx.Client(transport=httpx.MockTransport(record))
    return TelegramNotifier(TOKEN, CHAT_ID, client=client), requests


def ok(result) -> httpx.Response:
    return httpx.Response(200, json={"ok": True, "result": result})


def test_send_posts_message_and_returns_message_id():
    tg, requests = notifier(lambda r: ok({"message_id": 42}))
    assert tg.send("🍽️ Mesa 1 está terminando.") == 42
    (request,) = requests
    assert str(request.url) == f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    assert json.loads(request.content) == {
        "chat_id": CHAT_ID,
        "text": "🍽️ Mesa 1 está terminando.",
    }


def test_edit_posts_edit_message_text():
    tg, requests = notifier(lambda r: ok({"message_id": 42}))
    assert tg.edit(42, resolved_text("hola"))
    (request,) = requests
    assert request.url.path.endswith("/editMessageText")
    assert json.loads(request.content) == {
        "chat_id": CHAT_ID,
        "message_id": 42,
        "text": "hola\n\n✅ ya resuelto",
    }


def network_error(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError(f"no conecta a {request.url}", request=request)


@pytest.mark.parametrize(
    "handler",
    [
        network_error,
        lambda r: httpx.Response(
            400, json={"ok": False, "description": "Bad Request: chat not found"}
        ),
        lambda r: httpx.Response(502, text="<html>Bad Gateway</html>"),
    ],
    ids=["red", "ok_false", "no_json"],
)
def test_failures_are_logged_and_do_not_raise(handler, caplog):
    tg, _ = notifier(handler)
    with caplog.at_level(logging.WARNING):
        assert tg.send("hola") is None
        assert tg.edit(1, "hola") is False
    assert caplog.records
    assert TOKEN not in caplog.text


def test_httpx_does_not_log_the_url_with_the_token(caplog):
    tg, _ = notifier(lambda r: ok({"message_id": 1}))
    with caplog.at_level(logging.INFO):
        tg.send("hola")
    assert TOKEN not in caplog.text


def test_send_without_message_id_returns_none():
    tg, _ = notifier(lambda r: ok({"sin": "message_id"}))
    assert tg.send("hola") is None


def test_from_env_requires_both_variables():
    with pytest.raises(ValueError, match="TELEGRAM_GROUP_CHAT_ID"):
        TelegramNotifier.from_env({"TELEGRAM_BOT_TOKEN": TOKEN})
    tg = TelegramNotifier.from_env(
        {"TELEGRAM_BOT_TOKEN": TOKEN, "TELEGRAM_GROUP_CHAT_ID": CHAT_ID}
    )
    assert isinstance(tg, TelegramNotifier)


def test_get_notifier_falls_back_to_console(caplog):
    assert isinstance(get_notifier(telegram=False), ConsoleNotifier)
    with caplog.at_level(logging.WARNING):
        assert isinstance(get_notifier(telegram=True, env={}), ConsoleNotifier)
    assert "TELEGRAM_BOT_TOKEN" in caplog.text


def test_console_notifier_prints_with_increasing_ids(capsys):
    console = ConsoleNotifier()
    assert console.send("uno") == 1
    assert console.send("dos") == 2
    assert console.edit(1, resolved_text("uno"))
    out = capsys.readouterr().out
    assert "[telegram #1] uno" in out
    assert "[telegram editado #1] uno\n\n✅ ya resuelto" in out


def test_console_notifier_survives_a_console_without_emojis(monkeypatch):
    # Windows con stdout redirigido: cp1252 no tiene ❓ ni ✅.
    raw = io.BytesIO()
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(raw, encoding="cp1252"))
    console = ConsoleNotifier()
    assert console.send("❓ Mesa 1: un plato sigue lleno hace 2 min.") == 1
    assert console.edit(1, resolved_text("❓ Mesa 1"))
    sys.stdout.flush()
    out = raw.getvalue().decode("cp1252")
    assert "[telegram #1] ? Mesa 1: un plato sigue lleno" in out
    assert "? ya resuelto" in out
