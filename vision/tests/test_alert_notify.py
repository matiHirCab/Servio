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
    tg, requests, _ = retrying(handler)
    return tg, requests


def retrying(handler) -> tuple[TelegramNotifier, list[httpx.Request], list[float]]:
    """Como `notifier`, más las esperas entre reintentos (sin dormir de verdad)."""
    requests: list[httpx.Request] = []
    sleeps: list[float] = []

    def record(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    client = httpx.Client(transport=httpx.MockTransport(record))
    tg = TelegramNotifier(TOKEN, CHAT_ID, client=client, sleep=sleeps.append)
    return tg, requests, sleeps


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


def too_many_requests(retry_after: int) -> httpx.Response:
    return httpx.Response(
        429,
        json={
            "ok": False,
            "description": "Too Many Requests: retry later",
            "parameters": {"retry_after": retry_after},
        },
    )


def test_send_waits_retry_after_on_429_and_delivers():
    answers = iter([too_many_requests(3), ok({"message_id": 7})])
    tg, requests, sleeps = retrying(lambda r: next(answers))
    assert tg.send("hola") == 7
    assert len(requests) == 2
    assert sleeps == [3]


def test_send_retries_a_network_error_then_delivers():
    calls = iter([None, None, ok({"message_id": 8})])

    def handler(request):
        answer = next(calls)
        if answer is None:
            return network_error(request)
        return answer

    tg, requests, sleeps = retrying(handler)
    assert tg.send("hola") == 8
    assert len(requests) == 3
    assert len(sleeps) == 2


def test_send_retries_a_server_error():
    answers = iter([httpx.Response(502, text="bad gateway"), ok({"message_id": 9})])
    tg, requests, _ = retrying(lambda r: next(answers))
    assert tg.send("hola") == 9
    assert len(requests) == 2


def test_send_gives_up_after_a_few_attempts_and_does_not_raise(caplog):
    tg, requests, sleeps = retrying(network_error)
    with caplog.at_level(logging.WARNING):
        assert tg.send("hola") is None
    assert len(requests) == 3
    assert len(sleeps) == 2
    assert TOKEN not in caplog.text


def test_a_client_error_is_not_retried():
    tg, requests, sleeps = retrying(
        lambda r: httpx.Response(
            400, json={"ok": False, "description": "Bad Request: chat not found"}
        )
    )
    assert tg.send("hola") is None
    assert len(requests) == 1
    assert sleeps == []


def test_retry_after_is_capped_so_the_loop_is_not_stuck():
    answers = iter([too_many_requests(3600), ok({"message_id": 1})])
    tg, _, sleeps = retrying(lambda r: next(answers))
    assert tg.send("hola") == 1
    assert sleeps and sleeps[0] <= 10


def test_get_updates_is_not_retried_inside_the_call():
    """El long polling ya lo repite el bucle de feedback.py; reintentar acá lo
    trabaría con esperas largas mientras llegan botones."""
    tg, requests, sleeps = retrying(network_error)
    assert tg.get_updates(None) == []
    assert len(requests) == 1
    assert sleeps == []


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


def test_console_notifier_never_raises_even_if_the_fallback_fails(monkeypatch, caplog):
    # stdout que dice "utf-8" pero no acepta nada fuera de ASCII (wrapper raro).
    class LyingStdout(io.StringIO):
        encoding = "utf-8"

        def write(self, text):
            text.encode("ascii")
            return super().write(text)

    monkeypatch.setattr(sys, "stdout", LyingStdout())
    console = ConsoleNotifier()
    assert console.send("❓ Mesa 1") == 1
    assert console.edit(1, "✅")
    assert "No se pudo imprimir" in caplog.text
