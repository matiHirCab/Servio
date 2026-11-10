"""Datos del restaurante y respuestas de los mozos (Telegram falso)."""

import threading
from datetime import UTC, datetime

from servio_vision.alerts.feedback import FeedbackDesk, Question
from servio_vision.restaurant import Restaurant


class FakeTelegram:
    """Registra lo que se manda. Sin `get_updates`: no es un canal que se escuche,
    así un `AlertProcessor` con él no arranca el hilo del bot (los tests llaman a
    `bot.handle` a mano, con `Listening`)."""

    def __init__(self):
        self.sent, self.edits, self.prompts, self.answers, self.deleted = (
            [],
            [],
            [],
            [],
            [],
        )
        self.keyboards: list = []  # el teclado de cada edición (None: sin botones)

    def send(self, text, buttons=None):
        self.sent.append((text, buttons))
        return 100 + len(self.sent)

    def edit(self, message_id, text, buttons=None):
        self.edits.append((message_id, text))
        self.keyboards.append(buttons)
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


class Listening(FakeTelegram):
    """Un canal que se escucha (arma el bot), pero que nunca trae nada."""

    def get_updates(self, offset, timeout=20):
        threading.Event().wait(0.05)  # el hilo del bot no gira en vacío
        return []


def question():
    return Question(
        alert_id="a1",
        table_id="T1",
        alert_type="plato_lleno_mucho",
        dish="Donas glaseadas",
        waiter="Juan",
        text="Mesa 1 ❓ Donas glaseadas sigue lleno hace 1 min · Juan.",
        answers=("Está todo bien", "No le gustaron"),
        asked_at=datetime.now(UTC),
    )


def desk():
    logged = []
    return FeedbackDesk(FakeTelegram(), logged.append), logged


def labels(buttons) -> list[list[str]]:
    return [[label for label, _ in row] for row in buttons or []]


def button(buttons, label: str) -> str:
    """El `callback_data` del botón con ese texto."""
    (data,) = [d for row in buttons for text, d in row if text == label]
    return data


def tap(d, data: str, who: str = "Ana", cid: str = "c1") -> None:
    d.handle({"callback_query": {"id": cid, "data": data, "from": {"first_name": who}}})


def reply(d, message_id: int | None, text: str, who: str = "Ana") -> None:
    message = {"chat": {"id": -1}, "from": {"first_name": who}, "text": text}
    if message_id is not None:
        message["reply_to_message"] = {"message_id": message_id}
    d.handle({"message": message})


def test_restaurant_assigns_order_by_appearance():
    r = Restaurant.load()
    r.see_plates("T1", ["T1-p2", "T1-p1"])  # aparecen juntos: se ordena por número
    assert r.dish("T1", "T1-p1").name == "Cookies con chips de chocolate"
    assert r.dish("T1", "T1-p2").name == "Donas glaseadas"
    assert r.dish("T1", "T1-p7") is None  # el pedido tenía 2 platos
    assert r.waiter("T1") == "Juan"
    assert r.waiter("T99") is None


def test_buttons_have_voy_listo_the_dish_answers_other_and_no_hacia_falta():
    d, _ = desk()
    message_id = d.ask(question())
    _, buttons = d.notifier.sent[0]
    assert message_id == 101
    assert labels(buttons) == [
        ["Voy", "Listo"],
        ["Está todo bien"],
        ["No le gustaron"],
        ["✏️ Otra respuesta"],
        ["No hacía falta"],
    ]
    # el callback_data de Telegram tiene un tope de 64 bytes
    assert all(len(d.encode()) <= 64 for row in buttons for _, d in row)


def test_button_answer_is_logged_and_shown():
    d, logged = desk()
    d.ask(question())
    tap(d, button(d.notifier.sent[0][1], "No le gustaron"))
    [event] = logged
    assert event["event"] == "alert_feedback"
    assert event["action"] == "motivo"
    assert (event["dish"], event["waiter"], event["answer"]) == (
        "Donas glaseadas",
        "Juan",
        "No le gustaron",
    )
    assert event["answered_by"] == "Ana" and not event["free_text"]
    assert d.notifier.edits[-1][1].endswith("📝 No le gustaron")  # sin el nombre
    assert d.suffix(101) == "\n\n📝 No le gustaron"
    # con el motivo ya no se ofrecen los motivos: quedan Voy, Listo y No hacía falta
    assert labels(d.notifier.keyboards[-1]) == [["Voy", "Listo"], ["No hacía falta"]]


def test_other_answer_asks_to_write_and_takes_the_reply():
    d, logged = desk()
    d.ask(question())
    tap(d, button(d.notifier.sent[0][1], "✏️ Otra respuesta"))
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

    from servio_vision.dish_match import DishMatcher
    from servio_vision.pipeline import Pipeline
    from servio_vision.schemas import Item
    from servio_vision.zones import TableZone, Zones

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    zones = Zones(
        frame_size=(100, 100),
        tables=[
            TableZone(table_id="T1", name="Mesa 1", polygon=[(0, 0), (99, 0), (99, 99)])
        ],
    )
    pipeline = Pipeline(
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
    assert d.notifier.keyboards[-1] is None  # cerrado: sin botones
    tap(d, button(d.notifier.sent[0][1], "No le gustaron"))
    assert d.notifier.edits[-1][1] == "✅ cerrado\n\n📝 No le gustaron"
    assert d.notifier.keyboards[-1] is None  # el motivo tardío no revive el teclado
    assert len(logged) == 1


def test_second_tap_does_not_count_twice():
    d, logged = desk()
    d.ask(question())
    data = button(d.notifier.sent[0][1], "Está todo bien")
    for cid in ("c1", "c2"):
        tap(d, data, cid=cid)
    assert len(logged) == 1
    assert d.notifier.answers[-1] == "Ya respondieron este aviso"


def test_message_format_is_table_emoji_message_waiter(tmp_path):
    from servio_vision.alerts.config import AlertConfig
    from servio_vision.alerts.messages import mention_waiter
    from servio_vision.alerts.notify import ConsoleNotifier
    from servio_vision.alerts.processor import AlertProcessor

    p = AlertProcessor(
        AlertConfig(interval=5, t1=10, t2=10, reminder=10),
        ConsoleNotifier(),
        tmp_path / "eventos.jsonl",
        Restaurant.load(),
    )
    try:
        assert mention_waiter("Mesa 1 🍽️ terminó", "T1", p.restaurant.waiter("T1")) == (
            "Mesa 1 🍽️ terminó · Juan"
        )
    finally:
        p.close()


def test_metrics_command_sends_the_image(tmp_path):
    from servio_vision.alerts.config import AlertConfig
    from servio_vision.alerts.processor import AlertProcessor
    from servio_vision.db import Database

    sent = []

    class Photos(Listening):
        def send_photo(self, path, caption=""):
            sent.append((path, caption))
            return 1

    p = AlertProcessor(
        AlertConfig(interval=5, t1=10, t2=10, reminder=10),
        Photos(),
        tmp_path / "eventos.jsonl",
        Restaurant.load(),
        Database(tmp_path / "servio.db"),
    )
    try:
        p.bot.handle(
            {"message": {"chat": {"id": -1}, "text": "/metricas@servio_mozos_bot"}}
        )
    finally:
        p.close()
    [(path, caption)] = sent
    assert path.exists() and path.suffix == ".png"
    assert "Risotto de hongos" in caption  # el historial simulado
    problems = p.notifier.sent[-1][0]  # los problemas por plato, en otro mensaje
    assert "Risotto de hongos · vuelve lleno el" in problems
    assert "Estaba frío:" in problems and "Está todo bien" not in problems


def test_picking_up_the_plates_says_who_and_how_long(tmp_path):
    from servio_vision.alerts.config import AlertConfig
    from servio_vision.alerts.processor import AlertProcessor
    from servio_vision.db import Database
    from tests.test_alert_engine import obs

    db = Database(tmp_path / "servio.db")
    p = AlertProcessor(
        AlertConfig(interval=30, t1=60, t2=600, reminder=600),
        FakeTelegram(),
        tmp_path / "eventos.jsonl",
        Restaurant.load(),
        db,
    )
    vacios = (("T1-p1", "vacio", "centro"), ("T1-p2", "vacio", "izquierda"))
    try:
        for s in (0, 30):
            p.process(obs(s, *vacios))
        assert p.notifier.sent[-1][0] == "Mesa 1 🍽️ terminó · Juan"
        for s in (60, 90, 120, 150):  # retiraron los platos
            p.process(obs(s))
        [(row)] = db.query(
            "SELECT motivo_cierre FROM avisos WHERE tipo = 'mesa_terminada'"
        )
    finally:
        p.close()
    text = p.notifier.edits[-1][1]
    assert text.startswith("Mesa 1 🍽️ terminó · Juan\n\n✅ Juan retiró los platos ")
    assert (
        text.endswith(" después de que terminó la mesa.") and "ya resuelto" not in text
    )
    assert row["motivo_cierre"] == "retirados"


def test_reply_prompt_names_the_table_like_the_alert():
    from servio_vision.alerts.messages import reply_prompt, table_name

    assert reply_prompt("Donas", "T1").endswith("con Donas (Mesa 1).")
    assert reply_prompt(None, "A3").endswith(f"({table_name('A3')}).")  # no "Mesa 3"


# --- C-5: «Voy», «Listo», «No hacía falta», citas y escalado, de punta a punta ---

VACIOS = (("T1-p1", "vacio", "centro"), ("T1-p2", "vacio", "izquierda"))
SIN_PLATOS: tuple = ()


def served(tmp_path, notifier=None):
    """Un procesador con el restaurante, la base y el Telegram falso; reloj de
    pared fijo para que `seconds_to_answer` sea exacto."""
    from servio_vision.alerts.processor import AlertProcessor
    from servio_vision.db import Database
    from tests.test_alert_engine import PROD

    p = AlertProcessor(
        PROD,
        notifier or FakeTelegram(),
        tmp_path / "eventos.jsonl",
        Restaurant.load(),
        Database(tmp_path / "servio.db"),
    )
    clock = [datetime(2026, 10, 17, 20, 0, tzinfo=UTC)]
    p.feedback.wall = lambda: clock[0]
    return p, clock


def finished(p, until: int, start: int = 0) -> None:
    """Mesa 1 con los dos platos vacíos (mesa_terminada abre en la primera)."""
    from tests.test_alert_engine import obs

    for s in range(start, until + 1, 30):
        p.process(obs(s, *VACIOS))


def feedback_lines(p) -> list[dict]:
    import json

    lines = [json.loads(x) for x in p.log.path.read_text("utf-8").splitlines()]
    return [e for e in lines if e["event"] == "alert_feedback"]


def test_every_alert_gets_voy_listo_and_no_hacia_falta(tmp_path):
    p, _ = served(tmp_path)
    try:
        finished(p, 0)
        text, buttons = p.notifier.sent[-1]
        assert text == "Mesa 1 🍽️ terminó · Juan"
        assert labels(buttons) == [["Voy", "Listo"], ["No hacía falta"]]
    finally:
        p.close()


def test_voy_takes_the_alert_and_stops_the_reminders(tmp_path):
    from servio_vision.alerts.models import Alert

    p, clock = served(tmp_path)
    try:
        finished(p, 30)
        (_, buttons) = p.notifier.sent[-1]
        clock[0] = clock[0].replace(second=42)
        tap(p.feedback, button(buttons, "Voy"), who="Juan")
        (alert,) = p.open_alerts.values()
        assert isinstance(alert, Alert) and alert.answered_by == "Juan"
        assert p.notifier.edits[-1][1] == "Mesa 1 🍽️ terminó · Juan\n\n👟 Voy · Juan"
        assert labels(p.notifier.keyboards[-1]) == [["Listo"], ["No hacía falta"]]
        assert p.notifier.answers[-1] == "¡Gracias! Quedó registrado"
        # otro «Voy» no cuenta: ya lo tomó Juan
        tap(p.feedback, button(buttons, "Voy"), who="Sofía", cid="c2")
        assert p.notifier.answers[-1] == "Ya lo tomó Juan"
        sent = len(p.notifier.sent)
        finished(
            p, 900, start=60
        )  # 15 min más sin retirar: ni recordatorio ni escalado
        assert len(p.notifier.sent) == sent
        [event] = feedback_lines(p)
        assert (event["action"], event["answer"], event["answered_by"]) == (
            "voy",
            "Voy",
            "Juan",
        )
        assert event["seconds_to_answer"] == 42
        [row] = p.db.query("SELECT accion, texto, segundos FROM respuestas")
        assert (row["accion"], row["texto"], row["segundos"]) == ("voy", "Voy", 42)
    finally:
        p.close()


def test_listo_nudges_once_after_ten_minutes_then_escalates(tmp_path):
    p, _ = served(tmp_path)
    try:
        finished(p, 60)
        (_, buttons) = p.notifier.sent[-1]
        tap(p.feedback, button(buttons, "Listo"), who="Juan")
        (alert,) = p.open_alerts.values()
        assert alert.done_at is not None and alert.answered_by == "Juan"
        assert labels(p.notifier.keyboards[-1]) == [["No hacía falta"]]
        finished(p, 630, start=90)
        assert len(p.notifier.sent) == 1  # antes de los 10 min, nada
        finished(p, 660, start=660)  # 60 + 600: insiste una vez
        assert p.notifier.sent[-1][0] == (
            "Mesa 1 🍽️ dijiste «Listo» hace 10 min y los platos siguen en la mesa · Juan"
        )
        finished(p, 810, start=690)
        assert len(p.notifier.sent) == 2
        finished(p, 840, start=840)  # 660 + R: escala, sin decir que nadie respondió
        assert p.notifier.sent[-1][0] == (
            "Mesa 1 ⚠️ sigue sin resolver · Juan dijo «Listo» hace 13 min"
        )
        finished(p, 1500, start=870)
        assert len(p.notifier.sent) == 3  # y nada más
    finally:
        p.close()


def test_no_hacia_falta_closes_by_hand_and_does_not_reopen(tmp_path):
    from tests.test_alert_engine import obs

    p, _ = served(tmp_path)
    try:
        finished(p, 30)
        (_, buttons) = p.notifier.sent[-1]
        tap(p.feedback, button(buttons, "No hacía falta"), who="Juan")
        assert p.open_alerts == {}
        text = p.notifier.edits[-1][1]
        # un falso aviso no se muestra como «ya resuelto»
        assert text == (
            "Mesa 1 🍽️ terminó · Juan\n\n🚫 No hacía falta · Juan\n\n"
            "🚫 Cerrado como falso aviso: la cámara no lo va a volver a avisar."
        )
        assert p.notifier.keyboards[-1] is None
        # un botón del aviso ya cerrado no hace nada
        tap(p.feedback, button(buttons, "Listo"), cid="c2")
        assert p.notifier.answers[-1] == "Este aviso ya no está activo"
        finished(p, 600, start=60)  # la cámara sigue viendo la mesa terminada
        assert len(p.notifier.sent) == 1 and p.open_alerts == {}
        [row] = p.db.query("SELECT motivo_cierre FROM avisos")
        assert row["motivo_cierre"] == "no_hacia_falta"
        # retiran y sirven otra cosa: cuando se termine sí avisa
        p.process(obs(630))
        p.process(obs(660, ("T1-p3", "lleno", "centro")))
        p.process(obs(690, ("T1-p3", "vacio", "centro")))
        assert len(p.notifier.sent) == 2 and len(p.open_alerts) == 1
    finally:
        p.close()


def test_a_reply_quoting_the_alert_or_its_reminder_counts(tmp_path):
    p, _ = served(tmp_path)
    try:
        finished(p, 180)  # aviso (101) y el primer recordatorio (102)
        assert len(p.notifier.sent) == 2
        reply(p.feedback, None, "voy")  # suelta, en el grupo: no se adivina
        reply(p.feedback, 101, "¿quién va?")  # cita, pero no es una palabra clave
        assert next(iter(p.open_alerts.values())).answered_by is None
        reply(p.feedback, 102, "Ya voy!", who="Sofía")  # cita al recordatorio
        assert next(iter(p.open_alerts.values())).answered_by == "Sofía"
        assert p.notifier.edits[-1][0] == 101  # se edita el aviso, no el recordatorio
        reply(p.feedback, 999, "listo")  # cita a otro mensaje: nada
        assert next(iter(p.open_alerts.values())).done_at is None
        reply(p.feedback, 101, "Listo.")
        assert next(iter(p.open_alerts.values())).done_at is not None
        assert [e["action"] for e in feedback_lines(p)] == ["voy", "listo"]
    finally:
        p.close()


def test_a_quoted_free_text_on_a_full_plate_is_its_reason():
    d, logged = desk()
    d.ask(question())
    reply(d, 101, "Lo quería sin azúcar")
    [event] = logged
    assert (event["action"], event["answer"], event["free_text"]) == (
        "motivo",
        "Lo quería sin azúcar",
        True,
    )


def test_escalation_goes_to_the_group_and_can_be_quoted(tmp_path):
    p, _ = served(tmp_path)
    try:
        finished(p, 540)  # aviso a 0 s, recordatorios a 180 y 360, escala a 540
        texts = [t for t, _ in p.notifier.sent]
        assert texts[1:3] == [
            "Mesa 1 🍽️ terminó hace 3 min · Juan",
            "Mesa 1 🍽️ terminó hace 6 min · Juan",
        ]
        assert texts[3] == (
            "Mesa 1 ⚠️ sin atender hace 9 min · Juan no respondió a 3 mensajes"
        )
        assert p.notifier.sent[3][1] is None  # recordatorios y escalado, sin teclado
        (alert,) = p.open_alerts.values()
        assert alert.escalated_at is not None
        reply(p.feedback, 104, "voy", who="Encargado")
        assert next(iter(p.open_alerts.values())).answered_by == "Encargado"
    finally:
        p.close()


def test_questions_survive_a_snapshot():
    d, _ = desk()
    d.ask(question())
    tap(d, button(d.notifier.sent[0][1], "Voy"))
    saved = d.snapshot()
    again, _ = desk()
    again.restore(saved)
    tap(again, button(d.notifier.sent[0][1], "Voy"), who="Otro")
    assert again.notifier.answers == ["Ya lo tomó Ana"]


def test_a_typed_voy_after_listo_does_not_take_it_again(tmp_path):
    p, _ = served(tmp_path)
    try:
        finished(p, 0)
        tap(p.feedback, button(p.notifier.sent[0][1], "Listo"), who="Juan")
        reply(p.feedback, 101, "voy", who="Sofía")
        assert next(iter(p.open_alerts.values())).answered_by == "Juan"
        assert [e["action"] for e in feedback_lines(p)] == ["listo"]
    finally:
        p.close()


def test_the_first_voy_wins_in_the_message_the_log_and_the_core(tmp_path):
    """Dos «Voy» casi juntos: el segundo llega mientras el primero está en el núcleo.
    Gana el primero en todos lados."""
    p, _ = served(tmp_path)
    try:
        finished(p, 0)
        data = button(p.notifier.sent[0][1], "Voy")
        respond = p.respond

        def slow_respond(alert_id, action, who):
            if who == "Ana":  # mientras Ana está en el núcleo, toca Juan
                tap(p.feedback, data, who="Juan", cid="c2")
            return respond(alert_id, action, who)

        p.feedback.on_action = slow_respond
        tap(p.feedback, data, who="Ana")
        assert next(iter(p.open_alerts.values())).answered_by == "Ana"
        assert p.notifier.edits[-1][1].endswith("👟 Voy · Ana")
        assert [e["answered_by"] for e in feedback_lines(p)] == ["Ana"]
        assert "Ya lo tomó Ana" in p.notifier.answers
    finally:
        p.close()


def test_each_reminder_is_its_own_message_and_quoting_it_takes_only_that_alert(
    tmp_path,
):
    from servio_vision.alerts.config import load_config
    from servio_vision.alerts.processor import AlertProcessor
    from tests.test_alert_engine import obs

    demo = load_config(interval=30, demo=True, env={})  # T1 = T2 = R = 60 s
    p = AlertProcessor(demo, FakeTelegram(), tmp_path / "eventos.jsonl")
    try:
        vacio, lleno = ("T1-p1", "vacio", "centro"), ("T1-p2", "lleno", "abajo")
        for s in (0, 30, 60, 90, 120):
            p.process(obs(s, vacio, lleno))
        texts = [t for t, _ in p.notifier.sent]
        assert len(texts) == 4  # dos avisos y un recordatorio de cada uno
        reply(p.feedback, 104, "voy")  # cita el recordatorio del plato lleno
        taken = {a.type: a.answered_by for a in p.open_alerts.values()}
        assert taken == {"plato_vacio": None, "plato_lleno_mucho": "Ana"}
    finally:
        p.close()
