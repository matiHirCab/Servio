"""Resto de la API de Telegram de `TelegramNotifier` (respuestas de los mozos, fotos,
polling), sin red: httpx.MockTransport."""

import json

import httpx

from servio_vision.alerts.notify import ConsoleNotifier, TelegramNotifier, get_notifier

TOKEN = "123:secreto"
CHAT_ID = "-1001"


def notifier(handler):
    requests: list[httpx.Request] = []

    def record(request):
        requests.append(request)
        return handler(request)

    client = httpx.Client(transport=httpx.MockTransport(record))
    return TelegramNotifier(TOKEN, CHAT_ID, client=client), requests


def ok(result):
    return httpx.Response(200, json={"ok": True, "result": result})


def body(request):
    return json.loads(request.content)


def test_send_with_buttons_builds_an_inline_keyboard():
    tg, requests = notifier(lambda r: ok({"message_id": 1}))
    tg.send("hola", buttons=[[("Sí", "si"), ("No", "no")], [("Tal vez", "tv")]])
    assert body(requests[0])["reply_markup"] == {
        "inline_keyboard": [
            [
                {"text": "Sí", "callback_data": "si"},
                {"text": "No", "callback_data": "no"},
            ],
            [{"text": "Tal vez", "callback_data": "tv"}],
        ]
    }


def test_ask_reply_forces_a_reply_to_the_message():
    tg, requests = notifier(lambda r: ok({"message_id": 7}))
    assert tg.ask_reply("¿Qué pasó?", reply_to=3) == 7
    payload = body(requests[0])
    assert requests[0].url.path.endswith("/sendMessage")
    assert payload["reply_to_message_id"] == 3
    assert payload["reply_markup"]["force_reply"] is True


def test_answer_callback_and_delete_hit_their_methods():
    tg, requests = notifier(lambda r: ok(True))
    tg.answer_callback("cb1", "Gracias")
    tg.delete(9)
    assert requests[0].url.path.endswith("/answerCallbackQuery")
    assert body(requests[0]) == {"callback_query_id": "cb1", "text": "Gracias"}
    assert requests[1].url.path.endswith("/deleteMessage")
    assert body(requests[1]) == {"chat_id": CHAT_ID, "message_id": 9}


def test_get_updates_sends_offset_and_returns_the_list():
    tg, requests = notifier(lambda r: ok([{"update_id": 5}]))
    assert tg.get_updates(offset=5, timeout=1) == [{"update_id": 5}]
    payload = body(requests[0])
    assert payload["offset"] == 5
    assert payload["allowed_updates"] == ["callback_query", "message"]


def test_get_updates_without_offset_omits_it():
    tg, requests = notifier(lambda r: ok([]))
    assert tg.get_updates(offset=None, timeout=1) == []
    assert "offset" not in body(requests[0])


def test_get_updates_returns_empty_when_telegram_fails():
    tg, _ = notifier(lambda r: httpx.Response(500, json={"ok": False}))
    assert tg.get_updates(offset=None, timeout=1) == []


def test_is_group_compares_as_strings():
    tg, _ = notifier(lambda r: ok(True))
    assert tg.is_group(-1001) and tg.is_group("-1001")
    assert not tg.is_group(42)


def test_send_photo_posts_a_multipart_form(tmp_path):
    image = tmp_path / "metricas.png"
    image.write_bytes(b"\x89PNG fake")
    tg, requests = notifier(lambda r: ok({"message_id": 11}))

    assert tg.send_photo(image, caption="Métricas") == 11

    request = requests[0]
    assert request.url.path.endswith("/sendPhoto")
    assert request.headers["content-type"].startswith("multipart/form-data")
    assert b"metricas.png" in request.content and b"M\xc3\xa9tricas" in request.content


def test_non_json_response_is_logged_without_the_token(caplog):
    tg, _ = notifier(lambda r: httpx.Response(502, text="<html>bad gateway</html>"))
    assert tg.send("hola") is None
    assert TOKEN not in caplog.text


def test_error_description_is_logged(caplog):
    tg, _ = notifier(
        lambda r: httpx.Response(
            400, json={"ok": False, "description": "chat not found"}
        )
    )
    assert tg.edit(1, "x") is False
    assert "chat not found" in caplog.text


def test_from_env_strips_whitespace_and_builds_a_notifier():
    tg = TelegramNotifier.from_env(
        {"TELEGRAM_BOT_TOKEN": " t ", "TELEGRAM_GROUP_CHAT_ID": " -5 "}
    )
    assert tg._token == "t" and tg._chat_id == "-5"


def test_get_notifier_with_credentials_builds_telegram():
    env = {"TELEGRAM_BOT_TOKEN": "t", "TELEGRAM_GROUP_CHAT_ID": "-5"}
    assert isinstance(get_notifier(True, env), TelegramNotifier)


def test_console_send_photo_prints_the_path(capsys, tmp_path):
    console = ConsoleNotifier()
    assert console.send_photo(tmp_path / "a.png", "pie") == 1
    assert "a.png" in capsys.readouterr().out


def test_get_notifier_without_telegram_is_console():
    assert isinstance(get_notifier(False), ConsoleNotifier)
