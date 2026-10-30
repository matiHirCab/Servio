"""Envío a Telegram (MAT-4). Ver docs/context/06-notificaciones.md.

Solo envío, con `httpx` directo a la Bot API: `send` abre un mensaje en el grupo y
devuelve su `message_id`; `edit` lo reescribe (al cerrar, con el texto de
`messages.resolved_text`).
Si Telegram falla se loguea y se sigue: nunca lanza, así el loop no se frena. Antes
de rendirse reintenta lo que puede ser pasajero (red, 5xx y el 429 de «esperá N
segundos»): un aviso perdido por un parpadeo de internet es un mozo que no se entera.
Un reintento después de un timeout puede duplicar un mensaje que sí llegó: se
prefiere eso a perder el aviso.

`send` y `edit` corren en el camino del loop de lecturas, así que tienen un plazo
(`SEND_DEADLINE_S`) y no lo pasan: cada intento se corta en lo que queda y no se espera
un `retry_after` más largo que el plazo. Lo que no llegó en ese plazo, si vale la pena
reintentar (red, 5xx, 429), va a una cola acotada que un hilo de fondo sigue
reintentando con espera creciente, hasta `BACKLOG_MAX_AGE_S` (un aviso más viejo ya no
sirve). Mientras haya cola, los envíos nuevos entran detrás sin esperar a una red que no
anda, y salen en orden cuando vuelve. Un mensaje que sale así, tarde, no le deja su
`message_id` al aviso (`send` ya devolvió `None`): el mozo lo recibe, pero el
aviso no se puede editar al cerrarse; igual que cuando Telegram falla del todo.

El agrupado por mesa y guardar el `message_id` en el `Alert` son de `outputs.py`
(`TelegramOutput`). Los textos, de `messages.py`. Escuchar respuestas y comandos
(`get_updates` y compañía) lo usa el bot (`bot.py`), que solo depende de `BotChannel`.
"""

import logging
import sys
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import httpx

from servio_vision import settings

logger = logging.getLogger(__name__)
# httpx loguea cada request en INFO con la URL completa, y la URL trae el token.
logging.getLogger("httpx").setLevel(logging.WARNING)

API_URL = "https://api.telegram.org"
SEND_ATTEMPTS = 3  # el primero y dos reintentos
RETRY_WAIT_S = 1.0  # espera entre intentos si Telegram no pide otra
RETRY_WAIT_MAX_S = 10.0  # tope por espera: el loop de lecturas espera al envío
SEND_DEADLINE_S = 8.0  # lo máximo que un send/edit hace esperar al loop
BACKLOG_MAX = 50  # mensajes en cola; pasado esto se descartan los más viejos
BACKLOG_MAX_AGE_S = 600.0  # un aviso más viejo que esto ya no sirve
BACKLOG_RETRY_MAX_S = 30.0  # la espera entre rondas de la cola crece hasta esto


@dataclass
class _Outcome:
    """Cómo terminó un envío: `ok`, o falló y `retryable` dice si vale reintentar."""

    result: Any = None
    ok: bool = False
    retryable: bool = False


@dataclass
class _Queued:
    method: str
    payload: dict[str, Any]
    queued_at: float


# Botones de un mensaje: filas de (texto, callback_data).
Buttons = list[list[tuple[str, str]]]


class Notifier(Protocol):
    def send(self, text: str, buttons: Buttons | None = None) -> int | None:
        """Manda un mensaje al grupo; devuelve su `message_id` o None si falló."""
        ...

    def edit(self, message_id: int, text: str) -> bool:
        """Reescribe un mensaje ya mandado (y le saca los botones); False si falló."""
        ...

    def send_photo(self, path: Path, caption: str = "") -> int | None:
        """Manda una imagen al grupo (las métricas de `/metricas`)."""
        ...


class TelegramNotifier:
    """Manda y edita mensajes en el grupo de mozos."""

    def __init__(
        self,
        token: str,
        chat_id: str,
        *,
        client: httpx.Client | None = None,
        timeout: float = 10.0,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        deadline_s: float = SEND_DEADLINE_S,
        background: bool = True,
    ) -> None:
        """`deadline_s`: lo máximo que un `send`/`edit` espera. `background`: un hilo
        reintenta la cola; sin él (tests) se vacía a mano con `retry_backlog`."""
        self._token = token
        self._chat_id = chat_id
        self._timeout = timeout
        self._client = client or httpx.Client(timeout=timeout)
        self._sleep = sleep  # los tests no duermen de verdad
        self._clock = clock
        self._deadline_s = deadline_s
        self._background = background
        self._backlog: deque[_Queued] = deque()
        self._backlog_lock = threading.Condition()
        self._worker: threading.Thread | None = None
        self._stop = threading.Event()

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "TelegramNotifier":
        """Token y grupo de `env`; sin `env`, de la configuración (settings.py)."""
        if env is None:
            cfg = settings.current()
            token, chat_id = (
                cfg.telegram_bot_token.strip(),
                cfg.telegram_group_chat_id.strip(),
            )
        else:
            token = env.get("TELEGRAM_BOT_TOKEN", "").strip()
            chat_id = env.get("TELEGRAM_GROUP_CHAT_ID", "").strip()
        missing = [
            name
            for name, value in (
                ("TELEGRAM_BOT_TOKEN", token),
                ("TELEGRAM_GROUP_CHAT_ID", chat_id),
            )
            if not value
        ]
        if missing:
            raise ValueError(f"faltan variables de Telegram: {', '.join(missing)}")
        return cls(token, chat_id)

    def send(self, text: str, buttons: Buttons | None = None) -> int | None:
        payload: dict[str, Any] = {"chat_id": self._chat_id, "text": text}
        if buttons:
            payload["reply_markup"] = _keyboard(buttons)
        return _message_id(self._deliver("sendMessage", payload))

    def edit(self, message_id: int, text: str) -> bool:
        payload = {"chat_id": self._chat_id, "message_id": message_id, "text": text}
        return self._deliver("editMessageText", payload) is not None

    # --- respuestas de los mozos (feedback.py) ---

    def ask_reply(self, text: str, reply_to: int) -> int | None:
        """Pide que respondan a este mensaje escribiendo (force_reply)."""
        payload = {
            "chat_id": self._chat_id,
            "text": text,
            "reply_to_message_id": reply_to,
            "reply_markup": {"force_reply": True, "selective": True},
        }
        return _message_id(
            self._call("sendMessage", payload, deadline_s=self._deadline_s)
        )

    def answer_callback(self, callback_id: str, text: str) -> None:
        self._call(
            "answerCallbackQuery",
            {"callback_query_id": callback_id, "text": text},
            deadline_s=self._deadline_s,
        )

    def delete(self, message_id: int) -> None:
        self._call(
            "deleteMessage",
            {"chat_id": self._chat_id, "message_id": message_id},
            deadline_s=self._deadline_s,
        )

    def get_updates(self, offset: int | None, timeout: int = 20) -> list[dict]:
        """Toques de botones y mensajes nuevos (long polling); [] si falla."""
        payload: dict[str, Any] = {
            "timeout": timeout,
            "allowed_updates": ["callback_query", "message"],
        }
        if offset is not None:
            payload["offset"] = offset
        # sin reintentos acá: el bucle de feedback.py ya repite el long polling
        result = self._call("getUpdates", payload, timeout=timeout + 10, attempts=1)
        return result if isinstance(result, list) else []

    def is_group(self, chat_id: object) -> bool:
        return str(chat_id) == str(self._chat_id)

    def send_photo(self, path: Path, caption: str = "") -> int | None:
        """Manda una imagen al grupo (por ejemplo, las métricas)."""
        files = {"photo": (path.name, path.read_bytes(), "image/png")}
        data = {"chat_id": self._chat_id, "caption": caption}
        return _message_id(
            self._call("sendPhoto", data, files=files, timeout=60, deadline_s=60)
        )

    def _call(
        self,
        method: str,
        payload: dict[str, Any],
        timeout: float | None = None,
        files: dict[str, Any] | None = None,
        attempts: int = SEND_ATTEMPTS,
        deadline_s: float | None = None,
    ) -> Any:
        """POST a la Bot API; devuelve `result` o None si falló (ya logueado)."""
        return self._attempt(
            method, payload, timeout, files, attempts, deadline_s
        ).result

    def _attempt(
        self,
        method: str,
        payload: dict[str, Any],
        timeout: float | None = None,
        files: dict[str, Any] | None = None,
        attempts: int = SEND_ATTEMPTS,
        deadline_s: float | None = None,
    ) -> _Outcome:
        """POST a la Bot API. Con `files`, va como formulario (multipart) en vez de
        JSON. Reintenta hasta `attempts` veces un error pasajero (ver `_post_once`);
        con `deadline_s`, todos los intentos juntos entran en ese plazo: cada uno se
        corta en lo que queda y no se espera una pausa que ya no entra."""
        url = f"{API_URL}/bot{self._token}/{method}"
        end = None if deadline_s is None else self._clock() + deadline_s
        base_timeout = self._timeout if timeout is None else timeout
        outcome = _Outcome()
        for attempt in range(1, attempts + 1):
            body: dict[str, Any] = (
                {"data": payload, "files": files} if files else {"json": payload}
            )
            if end is None:
                if timeout is not None:
                    body["timeout"] = timeout
            else:
                remaining = end - self._clock()
                if remaining <= 0:
                    return _Outcome(retryable=True)
                body["timeout"] = min(base_timeout, remaining)
            ok, result, wait = self._post_once(method, url, body)
            if ok:
                return _Outcome(result, ok=True)
            if wait is None:  # un error que no se arregla solo (un 4xx)
                return _Outcome()
            outcome = _Outcome(retryable=True)
            if attempt == attempts:
                break
            if end is not None and wait >= end - self._clock():
                break  # esperar tanto ya no deja tiempo para otro intento
            self._sleep(wait)
        if attempts > 1:
            logger.warning("Telegram %s: me rindo (%d intentos máx.)", method, attempts)
        return outcome

    def _post_once(
        self, method: str, url: str, body: dict[str, Any]
    ) -> tuple[bool, Any, float | None]:
        """Un POST. Devuelve `(ok, result, espera)`: `espera` es `None` si no vale la
        pena reintentar, o los segundos a esperar antes del próximo intento."""
        try:
            response = self._client.post(url, **body)
        except httpx.HTTPError as error:
            # Sin el mensaje de httpx: trae la URL, y la URL trae el token.
            logger.warning("Telegram %s falló: %s", method, type(error).__name__)
            return False, None, RETRY_WAIT_S
        try:
            data = response.json()
        except ValueError:
            data = None
        if isinstance(data, dict) and data.get("ok"):
            return True, data.get("result"), None
        description = data.get("description") if isinstance(data, dict) else None
        logger.warning(
            "Telegram %s falló (HTTP %s): %s", method, response.status_code, description
        )
        if response.status_code == 429:
            return False, None, _retry_after(data)
        if response.status_code >= 500:
            return False, None, RETRY_WAIT_S
        return False, None, None

    # --- cola de lo que no llegó a tiempo ---

    @property
    def pending(self) -> int:
        """Mensajes en cola, esperando a que Telegram vuelva."""
        with self._backlog_lock:
            return len(self._backlog)

    @property
    def worker_running(self) -> bool:
        return self._worker is not None and self._worker.is_alive()

    def _deliver(self, method: str, payload: dict[str, Any]) -> Any:
        """`send`/`edit` con plazo. Con cola, el mensaje entra detrás (en orden y sin
        esperar a una red que no anda); si no llega en el plazo y vale reintentar,
        también va a la cola. Devuelve el `result`, o `None` si todavía no llegó."""
        if self.pending:
            self._enqueue(method, payload)
            return None
        outcome = self._attempt(method, payload, deadline_s=self._deadline_s)
        if outcome.ok:
            return outcome.result
        if outcome.retryable:
            self._enqueue(method, payload)
        return None

    def _enqueue(self, method: str, payload: dict[str, Any]) -> None:
        with self._backlog_lock:
            if len(self._backlog) >= BACKLOG_MAX:
                dropped = self._backlog.popleft()
                logger.error(
                    "Cola de Telegram llena (%d): descarto el mensaje más viejo (%s)",
                    BACKLOG_MAX,
                    dropped.method,
                )
            self._backlog.append(_Queued(method, payload, self._clock()))
            self._backlog_lock.notify()
        logger.warning("Telegram %s no llegó: queda en cola para reintentar", method)
        if self._background and not self.worker_running:
            self._worker = threading.Thread(
                target=self._run_backlog, name="telegram-backlog", daemon=True
            )
            self._worker.start()

    def retry_backlog(self) -> int:
        """Reintenta la cola en orden, un intento por mensaje, y frena en el primer
        fallo pasajero (la red sigue caída). Devuelve cuántos quedan."""
        while True:
            with self._backlog_lock:
                item = self._backlog[0] if self._backlog else None
            if item is None:
                return 0
            age = self._clock() - item.queued_at
            if age > BACKLOG_MAX_AGE_S:
                logger.error(
                    "Telegram %s: mensaje viejo (%.0f s en cola), lo descarto",
                    item.method,
                    age,
                )
                self._pop(item)
                continue
            outcome = self._attempt(
                item.method, item.payload, attempts=1, deadline_s=self._deadline_s
            )
            if outcome.ok:
                logger.info(
                    "Telegram %s entregado tras %.0f s en cola", item.method, age
                )
                self._pop(item)
            elif not outcome.retryable:
                self._pop(item)  # un 4xx: ya se logueó, reintentar no lo arregla
            else:
                return self.pending

    def _pop(self, item: _Queued) -> None:
        with self._backlog_lock:
            if self._backlog and self._backlog[0] is item:
                self._backlog.popleft()

    def _run_backlog(self) -> None:
        wait = RETRY_WAIT_S
        while not self._stop.is_set():
            with self._backlog_lock:
                while not self._backlog and not self._stop.is_set():
                    self._backlog_lock.wait(timeout=1.0)
            if self._stop.is_set():
                break
            if self.retry_backlog():
                self._stop.wait(wait)
                wait = min(wait * 2, BACKLOG_RETRY_MAX_S)
            else:
                wait = RETRY_WAIT_S

    def close(self) -> None:
        """Para el hilo de la cola. Lo que quedó sin entregar se pierde: se dice."""
        self._stop.set()
        with self._backlog_lock:
            self._backlog_lock.notify_all()
        if self._worker is not None:
            self._worker.join(timeout=2)
        left = self.pending
        if left:
            logger.warning("Telegram: %d mensajes quedaron sin entregar", left)


class ConsoleNotifier:
    """Modo `--no-telegram`: imprime los mensajes y simula los `message_id`."""

    def __init__(self) -> None:
        self._last_id = 0

    def send(self, text: str, buttons: Buttons | None = None) -> int:
        self._last_id += 1
        options = ""
        if buttons:
            options = " | botones: " + " / ".join(b[0] for row in buttons for b in row)
        _print(f"[telegram #{self._last_id}] {text}{options}")
        return self._last_id

    def edit(self, message_id: int, text: str) -> bool:
        _print(f"[telegram editado #{message_id}] {text}")
        return True

    def send_photo(self, path: Path, caption: str = "") -> int:
        self._last_id += 1
        _print(f"[telegram #{self._last_id}] (imagen {path}) {caption}")
        return self._last_id


def _keyboard(buttons: Buttons) -> dict[str, Any]:
    return {
        "inline_keyboard": [
            [{"text": label, "callback_data": data} for label, data in row]
            for row in buttons
        ]
    }


def _retry_after(data: Any) -> float:
    """Segundos que pide Telegram en un 429, con tope; sin dato, la espera normal."""
    params = data.get("parameters") if isinstance(data, dict) else None
    seconds = params.get("retry_after") if isinstance(params, dict) else None
    if isinstance(seconds, int | float) and not isinstance(seconds, bool):
        return min(max(float(seconds), 0.0), RETRY_WAIT_MAX_S)
    return RETRY_WAIT_S


def _message_id(result: Any) -> int | None:
    if not isinstance(result, dict) or not isinstance(result.get("message_id"), int):
        return None
    return result["message_id"]


def _print(text: str) -> None:
    """Imprime sin fallar: en Windows, con stdout redirigido (cp1252), los emojis
    tiran `UnicodeEncodeError`; ahí salen como `?` en vez de cortar el aviso. Como
    `send`/`edit`, nunca lanza: si tampoco así se puede imprimir, se loguea."""
    try:
        print(text, flush=True)
    except UnicodeEncodeError:
        try:
            encoding = sys.stdout.encoding or "ascii"
            print(text.encode(encoding, "replace").decode(encoding), flush=True)
        except (UnicodeError, LookupError, OSError):
            logger.warning("No se pudo imprimir el aviso en consola")


def get_notifier(telegram: bool, env: Mapping[str, str] | None = None) -> Notifier:
    """Telegram si se pidió y hay credenciales; si no, consola."""
    if not telegram:
        return ConsoleNotifier()
    try:
        return TelegramNotifier.from_env(env)
    except ValueError as error:
        logger.warning("%s: imprimo los avisos en consola", error)
        return ConsoleNotifier()
