"""`DishMatcher`: qué plato del pedido es cada recorte (`servio_vision/dish_match.py`)."""

import json
import logging
from types import SimpleNamespace

from servio_vision.dish_match import DishMatcher
from servio_vision.restaurant import Dish

OPTIONS = [
    Dish("d1", "Milanesa", ("bien",)),
    Dish("d2", "Ensalada", ("bien",)),
    Dish("d3", "Pasta", ("bien",)),
]


class FakeMessages:
    def __init__(self, reply=None, error=None):
        self.reply = reply
        self.error = error
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        block = SimpleNamespace(type="text", text=self.reply)
        return SimpleNamespace(content=[block])


def matcher_with(messages):
    matcher = DishMatcher(model="modelo-de-prueba", api_key=None)
    matcher._client = SimpleNamespace(messages=messages)
    return matcher


def test_no_options_returns_none():
    assert DishMatcher(model="m", api_key=None).match(b"jpeg", []) is None


def test_without_api_key_uses_order_of_the_order():
    assert DishMatcher(model="m", api_key=None).match(b"jpeg", OPTIONS) == OPTIONS[0]


def test_single_option_does_not_call_the_model():
    messages = FakeMessages(reply=json.dumps({"opcion": 0}))
    assert matcher_with(messages).match(b"jpeg", OPTIONS[:1]) == OPTIONS[0]
    assert messages.calls == []


def test_model_choice_picks_the_option():
    messages = FakeMessages(reply=json.dumps({"opcion": 2}))
    assert matcher_with(messages).match(b"jpeg", OPTIONS) == OPTIONS[2]


def test_request_lists_options_and_limits_choice_to_them():
    messages = FakeMessages(reply=json.dumps({"opcion": 1}))
    matcher_with(messages).match(b"jpeg", OPTIONS)

    call = messages.calls[0]
    assert call["model"] == "modelo-de-prueba"
    text = call["messages"][0]["content"][1]["text"]
    assert "0. Milanesa" in text and "2. Pasta" in text
    schema = call["output_config"]["format"]["schema"]
    assert schema["properties"]["opcion"]["enum"] == [0, 1, 2]


def test_model_failure_falls_back_to_the_order(caplog):
    messages = FakeMessages(error=RuntimeError("sin red"))
    with caplog.at_level(logging.WARNING, logger="servio_vision.dish_match"):
        assert matcher_with(messages).match(b"jpeg", OPTIONS) == OPTIONS[0]
    assert "sin red" in caplog.text


def test_malformed_reply_falls_back_to_the_order():
    messages = FakeMessages(reply="no es json")
    assert matcher_with(messages).match(b"jpeg", OPTIONS) == OPTIONS[0]


def test_out_of_range_choice_falls_back_to_the_order():
    messages = FakeMessages(reply=json.dumps({"opcion": 9}))
    assert matcher_with(messages).match(b"jpeg", OPTIONS) == OPTIONS[0]
