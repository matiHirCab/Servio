"""`FeedbackDesk` se olvida de las preguntas que ya cerraron: en un servicio largo no crece."""

import dataclasses

from servio_vision.alerts.feedback import RESOLVED_GRACE_S, FeedbackDesk
from tests.test_feedback import FakeTelegram, question


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def make(telegram=None) -> tuple[FeedbackDesk, Clock]:
    clock = Clock()
    return FeedbackDesk(
        telegram or FakeTelegram(), lambda event: None, clock=clock
    ), clock


def ask(desk: FeedbackDesk, alert_id: str) -> int | None:
    return desk.ask(dataclasses.replace(question(), alert_id=alert_id))


def resolve(desk: FeedbackDesk, message_id: int) -> None:
    desk.rewrite(message_id, lambda answers: "✅ cerrado" + answers, resolved=True)


def held(desk: FeedbackDesk) -> tuple[int, int, int]:
    return len(desk._questions), len(desk._by_message), len(desk._prompts)


def test_a_resolved_question_is_kept_for_late_replies_and_then_forgotten():
    desk, clock = make()
    first = ask(desk, "a1")
    assert first is not None
    resolve(desk, first)
    token = next(iter(desk._questions))
    clock.now += RESOLVED_GRACE_S - 1
    ask(desk, "a2")  # cada pregunta nueva limpia lo vencido
    assert held(desk)[:2] == (2, 2)  # a1 sigue: todavía se le puede contestar

    clock.now += 2  # pasó el plazo
    ask(desk, "a3")
    assert held(desk)[:2] == (2, 2)  # a2 y a3: a1 se fue
    assert first not in desk._by_message
    # un toque tardío en sus botones viejos ya no la encuentra, y no rompe
    desk.handle({"callback_query": {"id": "c9", "data": f"fb:{token}:0", "from": {}}})
    assert desk.notifier.answers[-1] == "Este aviso ya no está activo"


def test_an_open_question_is_never_forgotten():
    """El aviso de plato lleno puede seguir abierto horas: sus botones tienen que
    seguir andando."""
    desk, clock = make()
    ask(desk, "a1")
    clock.now += 10 * 3600
    ask(desk, "a2")
    assert held(desk)[:2] == (2, 2)


def test_the_write_what_happened_prompt_goes_with_its_question():
    desk, clock = make()
    message_id = ask(desk, "a1")
    assert message_id is not None
    other = desk.notifier.sent[0][1][-1][0][1]  # el botón «Otra respuesta»
    desk.handle({"callback_query": {"id": "c1", "data": other, "from": {}}})
    assert held(desk)[2] == 1  # el pedido de «escribí qué pasó», sin contestar
    resolve(desk, message_id)
    clock.now += RESOLVED_GRACE_S + 1
    ask(desk, "a2")
    assert held(desk)[2] == 0


class DownTelegram(FakeTelegram):
    """Telegram no contesta ahora: el mensaje (con sus botones) queda en la cola de
    reintentos de `notify.py` y llega más tarde."""

    def send(self, text, buttons=None):
        self.sent.append((text, buttons))


def test_a_question_whose_message_is_queued_still_answers_when_it_arrives():
    """El mensaje que sale tarde por la cola trae los botones de esta pregunta: los
    toques tienen que encontrarla."""
    logged: list[dict] = []
    desk = FeedbackDesk(DownTelegram(), logged.append, clock=Clock())
    assert ask(desk, "a1") is None  # todavía sin message_id
    token = next(iter(desk._questions))
    desk.handle({"callback_query": {"id": "c1", "data": f"fb:{token}:0", "from": {}}})
    assert [e["event"] for e in logged] == ["alert_feedback"]


def test_a_question_without_a_message_expires_after_the_queue_gave_up():
    """Sin `message_id` nadie la va a cerrar con `rewrite`: vence sola, un rato más
    largo de lo que la cola de Telegram reintenta."""
    desk, clock = make(DownTelegram())
    ask(desk, "a1")
    assert held(desk) == (1, 0, 0)  # guardada, aunque sin mensaje
    clock.now += RESOLVED_GRACE_S + 1
    ask(desk, "a2")
    assert held(desk)[0] == 1  # a1 venció; queda solo a2


def test_memory_stays_small_over_many_alerts():
    desk, clock = make()
    for n in range(300):  # un aviso de plato lleno cada dos minutos, todos cierran
        message_id = ask(desk, f"a{n}")
        assert message_id is not None
        resolve(desk, message_id)
        clock.now += 120
    kept = held(desk)[0]
    assert kept <= RESOLVED_GRACE_S // 120 + 2  # los del plazo, no los 300
    assert held(desk)[0] == held(desk)[1]
