"""Respuestas de los mozos al aviso de plato lleno, desde Telegram.

El aviso de `plato_lleno_mucho` sale con botones: las respuestas típicas de ese plato
(`fixtures/restaurante/carta.json`) y "✏️ Otra respuesta". Si tocan un botón, la
respuesta queda registrada; con "Otra respuesta", el bot pide que escriban y toma el
texto que respondan a ese pedido. Cada respuesta:
- se guarda en el log (`alert_feedback`: plato, mozo, respuesta, quién y en cuánto),
  que alimenta las métricas de platos con problemas y de mozos;
- se agrega al mensaje del aviso ("📝 Juan: No le gustaron") y se sacan los botones.

Telegram se escucha con long polling (`getUpdates`) en un hilo aparte: no hace falta
servidor ni webhook. Con `--no-telegram` (ConsoleNotifier) no hay nada que escuchar.
"""

import logging
import secrets
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from servio_vision.restaurant import OTHER_ANSWER

logger = logging.getLogger(__name__)

OTHER = "otro"


@dataclass
class Question:
    alert_id: str
    table_id: str
    alert_type: str
    dish: str | None
    waiter: str | None
    text: str  # texto del aviso
    answers: tuple[str, ...]
    asked_at: datetime
    message_id: int | None = None
    feedback: list[str] = field(default_factory=list)  # líneas "📝 ..."
    answered: bool = False  # ya respondió alguien (un segundo toque no cuenta)
    # el aviso se cerró: arma el texto "ya resuelto" con las respuestas
    final: Callable[[str], str] | None = None


class FeedbackDesk:
    """Preguntas abiertas (por token de botón) y sus respuestas."""

    def __init__(
        self,
        notifier: Any,
        log_event: Callable[[dict], None],
        commands: dict[str, Callable[[], None]] | None = None,
    ) -> None:
        self.notifier = notifier
        self.log_event = log_event
        # comandos del grupo ("/metricas" → commands["metricas"]())
        self.commands = commands or {}
        self._questions: dict[str, Question] = {}  # token → pregunta
        self._by_message: dict[int, Question] = {}
        self._prompts: dict[int, str] = {}  # message_id del "escribí qué pasó" → token
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # --- preguntar ---

    def ask(self, question: Question) -> int | None:
        """Manda el aviso con los botones; devuelve el message_id."""
        token = secrets.token_hex(4)
        rows = [[(a, f"fb:{token}:{i}")] for i, a in enumerate(question.answers)]
        rows.append([(OTHER_ANSWER, f"fb:{token}:{OTHER}")])
        message_id = self.notifier.send(question.text, rows)
        question.message_id = message_id
        with self._lock:
            self._questions[token] = question
            if message_id is not None:
                self._by_message[message_id] = question
        return message_id

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
            text = build(self._suffix(question))
            if question is not None and resolved:
                question.final = build
            self.notifier.edit(message_id, text)
        return text

    # --- escuchar ---

    def start(self) -> None:
        if not hasattr(self.notifier, "get_updates"):
            return  # consola: no hay respuestas que escuchar
        self._thread = threading.Thread(
            target=self._listen, name="feedback", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _listen(self) -> None:
        # Arranca después de lo que quedó pendiente de corridas anteriores.
        pending = self.notifier.get_updates(offset=-1, timeout=0)
        offset = pending[-1]["update_id"] + 1 if pending else None
        logger.info("Escuchando respuestas de los mozos en Telegram")
        while not self._stop.is_set():
            updates = self.notifier.get_updates(offset)
            for update in updates:
                offset = update["update_id"] + 1
                try:
                    self.handle(update)
                except Exception:
                    logger.exception("No se pudo procesar una respuesta de Telegram")
            if not updates:
                self._stop.wait(0.5)

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
            self.notifier.answer_callback(
                callback["id"], "Este aviso ya no está activo"
            )
            return
        if question.answered:
            self.notifier.answer_callback(callback["id"], "Ya respondieron este aviso")
            return
        if choice == OTHER:
            self.notifier.answer_callback(callback["id"], "Escribí qué pasó")
            prompt = "✏️ Respondé a este mensaje contando qué pasó"
            if question.dish:
                prompt += f" con {question.dish}"
            prompt += f" ({_table_name(question.table_id)})."
            prompt_id = self.notifier.ask_reply(prompt, question.message_id)
            if prompt_id is not None:
                with self._lock:
                    self._prompts[prompt_id] = token
            return
        answer = question.answers[int(choice)]
        self.notifier.answer_callback(callback["id"], "¡Gracias! Quedó registrado")
        self._record(question, answer, who, free_text=False)

    def _on_message(self, message: dict) -> None:
        if not self.notifier.is_group(message.get("chat", {}).get("id")):
            return
        reply_to = (message.get("reply_to_message") or {}).get("message_id")
        text = (message.get("text") or "").strip()
        if text.startswith("/"):
            # "/metricas" o "/metricas@servio_mozos_bot"
            name = text.split()[0][1:].split("@")[0].lower()
            command = self.commands.get(name)
            if command is not None:
                logger.info("Comando /%s desde Telegram", name)
                command()
            return
        with self._lock:
            token = self._prompts.pop(reply_to, None) if reply_to else None
            question = self._questions.get(token) if token else None
        if question is None or not text or question.answered:
            return
        self.notifier.delete(
            reply_to
        )  # el pedido de "escribí qué pasó" ya no hace falta
        self._record(question, text, _name(message.get("from", {})), free_text=True)

    def _record(
        self, question: Question, answer: str, who: str, free_text: bool
    ) -> None:
        now = datetime.now(UTC)
        with self._lock:
            if question.answered:
                return  # dos toques casi juntos: cuenta el primero
            question.answered = True
            question.feedback.append(f"📝 {answer}")  # quién respondió queda en la base
            answers = self._suffix(question)
            # si el aviso ya se cerró, se mantiene el "ya resuelto"
            if question.final is not None:
                text = question.final(answers)
            else:
                text = question.text + answers
            if question.message_id is not None:
                self.notifier.edit(question.message_id, text)  # saca los botones
        self.log_event(
            {
                "event": "alert_feedback",
                "ts": now.isoformat().replace("+00:00", "Z"),
                "alert_id": question.alert_id,
                "table_id": question.table_id,
                "type": question.alert_type,
                "dish": question.dish,
                "waiter": question.waiter,
                "answer": answer,
                "free_text": free_text,
                "answered_by": who,
                "seconds_to_answer": round((now - question.asked_at).total_seconds()),
            }
        )
        logger.info("Respuesta de %s sobre %s: %s", who, question.dish, answer)


def _name(user: dict) -> str:
    return user.get("first_name") or user.get("username") or "Mozo"


def _table_name(table_id: str) -> str:
    return f"Mesa {table_id[1:]}" if table_id[1:].isdigit() else table_id
