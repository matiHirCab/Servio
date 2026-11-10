"""Respuestas de los mozos a los avisos, desde Telegram (A-4, C-5).

Cada aviso sale con botones: «Voy», «Listo» y «No hacía falta» en todos, y en el de
`plato_lleno_mucho` además las respuestas típicas de ese plato
(`fixtures/restaurante/carta.json`) y "✏️ Otra respuesta" (el bot pide que escriban y
toma el texto que respondan a ese pedido). Lo mismo vale escrito, citando el aviso
o un recordatorio (la respuesta rápida del reloj y del celular): «voy», «listo»,
«no hacía falta» (`messages.KEYWORDS`, comparadas enteras), o un motivo si el aviso
es de plato lleno. Un mensaje suelto, sin cita, se ignora: en el grupo no se sabe de
quién ni para qué mesa es (decisión 5; el chat por mozo es C-7).

Qué hace cada respuesta lo decide el núcleo (`core.respond`, vía `on_action`): «Voy»
toma el aviso y frena los recordatorios, «Listo» anota `done_at`, un motivo también
lo toma, y «No hacía falta» lo cierra. Acá cada respuesta:
- se guarda en el log (`alert_feedback`: plato, mozo, respuesta, acción, quién y en
  cuánto), que alimenta las métricas de platos con problemas y de mozos;
- se agrega al mensaje del aviso ("👟 Voy · Ana", "📝 No le gustaron") y el teclado
  queda con lo que todavía se puede tocar.

Todas las ediciones de un mensaje con pregunta pasan por `_refresh`, con el lock:
una respuesta que llega junto con el cierre no pisa el "ya resuelto" ni deja botones
vivos en un aviso cerrado.

Quien escucha el canal (long polling, comandos como `/metricas`) es el bot
(`bot.py`): le pasa a `handle` los toques de botón y los mensajes que no son comandos.
Los textos salen de `messages.py`.
"""

import logging
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from servio_vision.alerts import messages
from servio_vision.alerts.notify import Buttons
from servio_vision.restaurant import OTHER_ANSWER
from servio_vision.timestamps import iso

logger = logging.getLogger(__name__)

OTHER = "otro"
# datos de los botones: fb:{token}:{voy|listo|no|otro|índice del motivo}
BUTTON_ACTIONS = {"voy": "voy", "listo": "listo", "no": "no_hacia_falta"}
BUTTON_DATA = {action: data for data, action in BUTTON_ACTIONS.items()}
# Un aviso cerrado se sigue pudiendo contestar un rato: el mozo que tocó «Otra respuesta»
# justo antes del cierre escribe su texto después. Pasado esto se olvida la pregunta:
# sin eso, cada aviso de semanas quedaba en memoria (con el texto, los platos y el
# cierre). Un aviso abierto no se olvida nunca, dure lo que dure.
RESOLVED_GRACE_S = 1800.0

# `on_action(alert_id, acción, quién)`: lo que el núcleo hace con la respuesta
# (`AlertProcessor.respond`). False si el aviso ya no está abierto.
OnAction = Callable[[str, str, str], bool]


@dataclass
class Question:
    """El mensaje de un aviso, con sus botones y lo que ya respondieron."""

    alert_id: str
    table_id: str
    alert_type: str
    dish: str | None
    waiter: str | None
    text: str  # texto del aviso
    answers: tuple[str, ...]  # motivos del plato (vacío si no es plato lleno)
    asked_at: datetime  # reloj de pared: de acá sale `seconds_to_answer`
    message_id: int | None = None
    token: str = ""
    feedback: list[str] = field(default_factory=list)  # líneas "👟 …" / "📝 ..."
    taken_by: str | None = None  # quién dijo «Voy»
    done: bool = False  # ya dijeron «Listo»
    has_motive: bool = False  # ya contestaron el motivo (un segundo toque no cuenta)
    # recordatorios y escalados de este aviso: citarlos también responde al aviso
    extra_messages: list[int] = field(default_factory=list)
    # el aviso se cerró: arma el texto "ya resuelto" con las respuestas
    final: Callable[[str], str] | None = None
    resolved_at: float | None = None  # reloj del `FeedbackDesk`, cuando se cerró


def keyboard(question: Question) -> Buttons | None:
    """Los botones que quedan según lo que ya respondieron; None si el aviso cerró."""
    if question.final is not None:
        return None
    rows: Buttons = []
    top = []
    if question.taken_by is None and not question.done:  # «Listo» ya lo tomó
        top.append((messages.BUTTON_VOY, f"fb:{question.token}:voy"))
    if not question.done:
        top.append((messages.BUTTON_LISTO, f"fb:{question.token}:listo"))
    if top:
        rows.append(top)
    if question.answers and not question.has_motive:
        rows += [
            [(a, f"fb:{question.token}:{i}")] for i, a in enumerate(question.answers)
        ]
        rows.append([(OTHER_ANSWER, f"fb:{question.token}:{OTHER}")])
    rows.append([(messages.BUTTON_NO, f"fb:{question.token}:no")])
    return rows


class FeedbackDesk:
    """Preguntas abiertas (por token de botón) y sus respuestas."""

    def __init__(
        self,
        notifier: Any,
        log_event: Callable[[dict], None],
        on_action: OnAction | None = None,
        clock: Callable[[], float] = time.monotonic,
        wall: Callable[[], datetime] | None = None,
    ) -> None:
        self.notifier = notifier
        self.log_event = log_event
        self.on_action = on_action  # sin él (tests sueltos) toda respuesta vale
        self._clock = clock  # los tests lo adelantan
        self.wall = wall or (lambda: datetime.now(UTC))  # reloj de pared inyectable
        self._questions: dict[str, Question] = {}  # token → pregunta
        self._by_message: dict[int, Question] = {}  # aviso, recordatorios, escalados
        self._by_alert: dict[str, Question] = {}
        self._prompts: dict[int, str] = {}  # message_id del "escribí qué pasó" → token
        self._lock = threading.Lock()

    # --- preguntar ---

    def ask(self, question: Question) -> int | None:
        """Manda el aviso con los botones; devuelve el message_id."""
        question.token = secrets.token_hex(4)
        message_id = self.notifier.send(question.text, keyboard(question))
        question.message_id = message_id
        with self._lock:
            self._forget_resolved()
            self._register(question)
            if message_id is None:
                # Telegram no lo recibió ahora, pero `notify.py` lo reintenta: si sale
                # tarde, trae los botones de este token y tienen que encontrar la
                # pregunta. Sin `message_id` nadie la cierra con `rewrite`, así que
                # vence sola, más tarde de lo que la cola reintenta.
                question.resolved_at = self._clock()
        return message_id

    def _register(self, question: Question) -> None:
        self._questions[question.token] = question
        self._by_alert[question.alert_id] = question
        if question.message_id is not None:
            self._by_message[question.message_id] = question
        for message_id in question.extra_messages:
            self._by_message[message_id] = question

    def attach_message(self, message_id: int | None, alert_id: str) -> None:
        """Un recordatorio o escalado de ese aviso: citarlo también lo responde."""
        if message_id is None:
            return
        with self._lock:
            question = self._by_alert.get(alert_id)
            # un id que ya es de otro aviso no se pisa (Telegram no repite ids; un
            # canal que sí, como la consola tras un reinicio, no cambia de aviso)
            if question is not None and message_id not in self._by_message:
                question.extra_messages.append(message_id)
                self._by_message[message_id] = question

    def _forget_resolved(self) -> None:
        """Saca las preguntas cerradas hace más de `RESOLVED_GRACE_S`, con sus mensajes
        y sus pedidos de «escribí qué pasó». Con el lock tomado."""
        now = self._clock()
        expired = [
            token
            for token, q in self._questions.items()
            if q.resolved_at is not None and now - q.resolved_at > RESOLVED_GRACE_S
        ]
        for token in expired:
            question = self._questions.pop(token)
            if self._by_alert.get(question.alert_id) is question:
                del self._by_alert[question.alert_id]
            for message_id in [question.message_id, *question.extra_messages]:
                if message_id is not None:
                    self._by_message.pop(message_id, None)
            for prompt_id in [p for p, t in self._prompts.items() if t == token]:
                del self._prompts[prompt_id]

    def suffix(self, message_id: int) -> str:
        """Respuestas ya registradas de ese mensaje, para no perderlas al editarlo."""
        with self._lock:
            return self._suffix(self._by_message.get(message_id))

    @staticmethod
    def _suffix(question: Question | None) -> str:
        return "".join(f"\n\n{line}" for line in question.feedback) if question else ""

    def rewrite(
        self, message_id: int, build: Callable[[str], str], resolved: bool
    ) -> str:
        """Edita un mensaje con `build(respuestas)`. Todas las ediciones de un
        mensaje con preguntas pasan por acá, con el lock: una respuesta que llega
        justo cuando se cierra el aviso no pisa el "ya resuelto" (ni al revés)."""
        with self._lock:
            question = self._by_message.get(message_id)
            if question is None:
                text = build("")
                self.notifier.edit(message_id, text)
                return text
            if resolved:
                question.final = build
                question.resolved_at = self._clock()
            self._forget_resolved()
            return self._refresh(question)

    def _refresh(self, question: Question) -> str:
        """Reescribe el mensaje del aviso con sus respuestas y el teclado que queda.
        Con el lock tomado."""
        answers = self._suffix(question)
        if question.final is not None:
            text = question.final(answers)  # si el aviso ya se cerró, se mantiene
        else:
            text = question.text + answers
        if question.message_id is not None:
            self.notifier.edit(question.message_id, text, keyboard(question))
        return text

    # --- respuestas (las trae el bot) ---

    def handle(self, update: dict) -> None:
        if "callback_query" in update:
            self._on_button(update["callback_query"])
        elif "message" in update:
            self._on_message(update["message"])

    def _on_button(self, callback: dict) -> None:
        data = callback.get("data", "")
        if not data.startswith("fb:"):
            return
        _, token, choice = data.split(":", 2)
        with self._lock:
            question = self._questions.get(token)
        who = _name(callback.get("from", {}))
        if question is None:
            self.notifier.answer_callback(callback["id"], messages.BUTTON_EXPIRED)
            return
        if choice in BUTTON_ACTIONS:
            toast = self._action(question, BUTTON_ACTIONS[choice], who)
            self.notifier.answer_callback(callback["id"], toast)
            return
        if question.has_motive:
            self.notifier.answer_callback(
                callback["id"], messages.BUTTON_ALREADY_ANSWERED
            )
            return
        if choice == OTHER:
            self.notifier.answer_callback(callback["id"], messages.BUTTON_WRITE)
            prompt = messages.reply_prompt(question.dish, question.table_id)
            prompt_id = self.notifier.ask_reply(prompt, question.message_id)
            if prompt_id is not None:
                with self._lock:
                    self._prompts[prompt_id] = token
            return
        answer = question.answers[int(choice)]
        self.notifier.answer_callback(callback["id"], messages.BUTTON_THANKS)
        self._motive(question, answer, who, free_text=False)

    def _on_message(self, message: dict) -> None:
        if not self.notifier.is_group(message.get("chat", {}).get("id")):
            return
        reply_to = (message.get("reply_to_message") or {}).get("message_id")
        text = (message.get("text") or "").strip()
        if not reply_to or not text:
            return  # un mensaje suelto no se adivina (decisión 5)
        who = _name(message.get("from", {}))
        with self._lock:
            token = self._prompts.pop(reply_to, None)
            question = self._questions.get(token) if token else None
            quoted = self._by_message.get(reply_to)
        if question is not None:
            # respuesta al «escribí qué pasó»
            if question.has_motive:
                return
            self.notifier.delete(reply_to)  # el pedido ya no hace falta
            self._motive(question, text, who, free_text=True)
            return
        if quoted is None:
            return  # cita a un mensaje que no es un aviso
        action = messages.keyword_action(text)
        if action is not None:
            self._action(quoted, action, who)
        elif quoted.answers and not quoted.has_motive and quoted.final is None:
            self._motive(quoted, text, who, free_text=True)
        # otro texto citando un aviso sin motivos (o ya cerrado): se ignora

    def _action(self, question: Question, action: str, who: str) -> str:
        """«Voy», «Listo» o «No hacía falta»: lo aplica el núcleo y queda en el
        mensaje y el log. Devuelve el texto para el toque del botón.

        Quién gana se decide acá, con el lock y antes del núcleo: de dos «Voy» casi
        juntos, el mensaje, el log y el núcleo nombran al mismo."""
        with self._lock:
            if question.final is not None:
                return messages.BUTTON_EXPIRED
            if action == "voy":
                if question.taken_by is not None:
                    return messages.BUTTON_TAKEN.format(who=question.taken_by)
                if question.done:  # un «voy» escrito después de «Listo»
                    return messages.BUTTON_ALREADY_DONE
                question.taken_by = who
            elif action == "listo":
                if question.done:
                    return messages.BUTTON_ALREADY_DONE
                question.done = True
        if not self._act(question, action, who):
            with self._lock:  # el aviso ya se había cerrado: no queda tomado
                if action == "voy" and question.taken_by == who:
                    question.taken_by = None
                elif action == "listo":
                    question.done = False
            return messages.BUTTON_EXPIRED
        self._record(question, messages.ACTION_LABELS[action], who, False, action)
        return messages.BUTTON_THANKS

    def _motive(
        self, question: Question, answer: str, who: str, *, free_text: bool
    ) -> None:
        """Un motivo del plato lleno: toma el aviso (si estaba sin tomar) y queda
        registrado aunque el aviso ya se haya cerrado."""
        self._act(question, "motivo", who)
        self._record(question, answer, who, free_text, "motivo")

    def _act(self, question: Question, action: str, who: str) -> bool:
        """Lo que hace el núcleo con la respuesta. Sin el lock: el núcleo edita el
        mensaje (cierre) por `rewrite`, que lo toma."""
        if self.on_action is None:
            return True
        return self.on_action(question.alert_id, action, who)

    def _record(
        self, question: Question, answer: str, who: str, free_text: bool, action: str
    ) -> None:
        now = self.wall()
        with self._lock:
            if action == "motivo":
                if question.has_motive:
                    return  # dos toques casi juntos: cuenta el primero
                question.has_motive = True
                line = messages.feedback_line(answer)
            else:
                # «Voy» y «Listo» ya quedaron anotados en `_action`
                line = messages.action_line(action, who)
            question.feedback.append(line)
            self._refresh(question)
        self.log_event(
            {
                "event": "alert_feedback",
                "ts": iso(now),
                "alert_id": question.alert_id,
                "table_id": question.table_id,
                "type": question.alert_type,
                "dish": question.dish,
                "waiter": question.waiter,
                "answer": answer,
                "action": action,
                "free_text": free_text,
                "answered_by": who,
                "seconds_to_answer": round((now - question.asked_at).total_seconds()),
            }
        )
        logger.info("Respuesta de %s sobre %s: %s", who, question.table_id, answer)

    # --- para seguir después de un reinicio (persist.py) ---

    def snapshot(self) -> list[dict[str, Any]]:
        """Las preguntas de los avisos todavía abiertos, para la foto del estado."""
        with self._lock:
            return [
                {
                    "token": q.token,
                    "alert_id": q.alert_id,
                    "table_id": q.table_id,
                    "alert_type": q.alert_type,
                    "dish": q.dish,
                    "waiter": q.waiter,
                    "text": q.text,
                    "answers": list(q.answers),
                    "asked_at": iso(q.asked_at),
                    "message_id": q.message_id,
                    "feedback": list(q.feedback),
                    "taken_by": q.taken_by,
                    "done": q.done,
                    "has_motive": q.has_motive,
                    "extra_messages": list(q.extra_messages),
                }
                for q in self._questions.values()
                if q.final is None and q.message_id is not None
            ]

    def restore(self, questions: list[dict[str, Any]]) -> None:
        """Vuelve a escuchar los botones de los avisos de la corrida anterior."""
        with self._lock:
            for data in questions:
                question = Question(
                    alert_id=data["alert_id"],
                    table_id=data["table_id"],
                    alert_type=data["alert_type"],
                    dish=data.get("dish"),
                    waiter=data.get("waiter"),
                    text=data["text"],
                    answers=tuple(data.get("answers", ())),
                    asked_at=datetime.fromisoformat(data["asked_at"]),
                    message_id=data.get("message_id"),
                    token=data["token"],
                    feedback=list(data.get("feedback", [])),
                    taken_by=data.get("taken_by"),
                    done=bool(data.get("done", False)),
                    has_motive=bool(data.get("has_motive", False)),
                    extra_messages=[int(m) for m in data.get("extra_messages", [])],
                )
                self._register(question)


def _name(user: dict) -> str:
    return user.get("first_name") or user.get("username") or "Mozo"
