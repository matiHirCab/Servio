"""Datos del restaurante y respuestas de los mozos (Telegram falso)."""

from datetime import UTC, datetime

from servio_vision.alerts.feedback import FeedbackDesk, Question
from servio_vision.restaurant import Restaurant


class FakeTelegram:
    """Registra lo que se manda; `get_updates` existe pero no se usa (no se escucha)."""

    def __init__(self):
        self.sent, self.edits, self.prompts, self.answers, self.deleted = (
            [],
            [],
            [],
            [],
            [],
        )

    def send(self, text, buttons=None):
        self.sent.append((text, buttons))
        return 100 + len(self.sent)

    def edit(self, message_id, text):
        self.edits.append((message_id, text))
        return True

    def ask_reply(self, text, reply_to):
        self.prompts.append((text, reply_to))
        return 900

    def answer_callback(self, callback_id, text):
        self.answers.append(text)

    def delete(self, message_id):
        self.deleted.append(message_id)

    def is_group(self, chat_id):
        return chat_id == -1


def question():
    return Question(
        alert_id="a1",
        table_id="T1",
        alert_type="plato_lleno_mucho",
        dish="Donas glaseadas",
        waiter="Juan",
        text="❓ Juan · Mesa 1: Donas glaseadas sigue lleno hace 1 min.",
        answers=("Está todo bien", "No le gustaron"),
        asked_at=datetime.now(UTC),
    )


def desk():
    logged = []
    return FeedbackDesk(FakeTelegram(), logged.append), logged


def test_restaurant_assigns_order_by_appearance():
    r = Restaurant.load()
    r.see_plates("T1", ["T1-p2", "T1-p1"])  # aparecen juntos: se ordena por número
    assert r.dish("T1", "T1-p1").name == "Cookies con chips de chocolate"
    assert r.dish("T1", "T1-p2").name == "Donas glaseadas"
    assert r.dish("T1", "T1-p7") is None  # el pedido tenía 2 platos
    assert r.waiter("T1") == "Juan"
    assert r.waiter("T99") is None


def test_buttons_have_the_dish_answers_and_other():
    d, _ = desk()
    message_id = d.ask(question())
    _, buttons = d.notifier.sent[0]
    assert message_id == 101
    assert [row[0][0] for row in buttons] == [
        "Está todo bien",
        "No le gustaron",
        "✏️ Otra respuesta",
    ]


def test_button_answer_is_logged_and_shown():
    d, logged = desk()
    d.ask(question())
    data = d.notifier.sent[0][1][1][0][1]  # "No le gustaron"
    d.handle(
        {"callback_query": {"id": "c1", "data": data, "from": {"first_name": "Ana"}}}
    )
    [event] = logged
    assert event["event"] == "alert_feedback"
    assert (event["dish"], event["waiter"], event["answer"]) == (
        "Donas glaseadas",
        "Juan",
        "No le gustaron",
    )
    assert event["answered_by"] == "Ana" and not event["free_text"]
    assert d.notifier.edits[-1][1].endswith("📝 No le gustaron")  # sin el nombre
    assert d.suffix(101) == "\n\n📝 No le gustaron"


def test_other_answer_asks_to_write_and_takes_the_reply():
    d, logged = desk()
    d.ask(question())
    other = d.notifier.sent[0][1][-1][0][1]
    d.handle(
        {"callback_query": {"id": "c1", "data": other, "from": {"first_name": "Ana"}}}
    )
    assert "Respondé a este mensaje" in d.notifier.prompts[0][0]
    d.handle(
        {
            "message": {
                "chat": {"id": -1},
                "from": {"first_name": "Ana"},
                "text": "Se le cayó el café encima",
                "reply_to_message": {"message_id": 900},
            }
        }
    )
    [event] = logged
    assert event["answer"] == "Se le cayó el café encima" and event["free_text"]
    assert d.notifier.deleted == [900]


def test_unknown_button_does_not_break():
    d, logged = desk()
    d.handle({"callback_query": {"id": "c1", "data": "fb:nope:0", "from": {}}})
    assert logged == [] and d.notifier.answers == ["Este aviso ya no está activo"]


def test_without_api_key_dishes_follow_the_order(monkeypatch):
    from servio_vision.dish_match import DishMatcher

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    r = Restaurant.load()
    options = r.unassigned("T1")
    assert [d.name for d in options] == [
        "Cookies con chips de chocolate",
        "Donas glaseadas",
    ]
    assert DishMatcher().match(b"jpeg", options) == options[0]


def test_plates_not_in_the_order_are_dropped(monkeypatch):
    import numpy as np

    import main as loop
    from servio_vision.dish_match import DishMatcher
    from servio_vision.schemas import Item
    from servio_vision.zones import TableZone, Zones

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    zones = Zones(
        frame_size=(100, 100),
        tables=[
            TableZone(table_id="T1", name="Mesa 1", polygon=[(0, 0), (99, 0), (99, 99)])
        ],
    )
    pipeline = loop.Pipeline(
        None,
        zones,
        lambda o: None,
        restaurant=Restaurant.load(),
        dish_matcher=DishMatcher(),
    )
    plates = [
        Item(
            id=f"p{n}",
            state="lleno",
            position="c",
            confidence=1,
            bbox=(0.1, 0.1, 0.2, 0.2),
        )
        for n in (
            1,
            2,
            3,
        )  # el pedido de T1 tiene 2 platos: el 3.º es una caja, una taza…
    ]
    frame = np.zeros((100, 100, 3), np.uint8)
    pipeline._identify_dishes(frame, "T1", plates)
    kept = pipeline._only_ordered("T1", plates)
    pipeline.close()
    assert [i.id for i in kept] == ["p1", "p2"]


# --- regresiones (code review del PR #38) ---


def test_dishes_never_repeat_when_assigned_out_of_order():
    r = Restaurant.load()
    r.assign("T1", "T1-p1", r.dishes["donas"])  # la visión asigna fuera de orden
    # al siguiente le toca el que falta, no "el 2.º del pedido" (que es donas otra vez)
    assert r.dish("T1", "T1-p2").name == "Cookies con chips de chocolate"
    assert r.dish("T1", "T1-p3") is None


def test_a_plate_that_comes_back_with_a_new_id_keeps_its_dish():
    r = Restaurant.load()
    r.see_plates("T1", ["T1-p1", "T1-p2"])
    # YOLO perdió las donas y volvieron como p5: p2 dejó de seguirse
    r.release_missing("T1", {"T1-p1", "T1-p5"})
    assert r.dish("T1", "T1-p5").name == "Donas glaseadas"


def test_late_answer_keeps_the_resolved_mark():
    d, logged = desk()
    d.ask(question())
    d.rewrite(101, lambda answers: "✅ cerrado" + answers, resolved=True)
    data = d.notifier.sent[0][1][1][0][1]
    d.handle({"callback_query": {"id": "c1", "data": data, "from": {}}})
    assert d.notifier.edits[-1][1] == "✅ cerrado\n\n📝 No le gustaron"
    assert len(logged) == 1


def test_second_tap_does_not_count_twice():
    d, logged = desk()
    d.ask(question())
    data = d.notifier.sent[0][1][0][0][1]
    for cid in ("c1", "c2"):
        d.handle({"callback_query": {"id": cid, "data": data, "from": {}}})
    assert len(logged) == 1
    assert d.notifier.answers[-1] == "Ya respondieron este aviso"


def test_message_format_is_emoji_waiter_table_message(tmp_path):
    from servio_vision.alerts.config import AlertConfig
    from servio_vision.alerts.notify import ConsoleNotifier
    from servio_vision.alerts.processor import AlertProcessor

    p = AlertProcessor(
        AlertConfig(interval=5, t1=10, t2=10, reminder=10),
        ConsoleNotifier(),
        tmp_path / "eventos.jsonl",
        Restaurant.load(),
    )
    try:
        assert p._mention_waiter("🍽️ Mesa 1: terminó.", "T1") == (
            "🍽️ Juan · Mesa 1: terminó."
        )
    finally:
        p.close()
