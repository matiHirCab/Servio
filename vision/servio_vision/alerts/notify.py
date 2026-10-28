"""Envío a Telegram (MAT-4). Ver docs/context/06-notificaciones.md.

Solo envío, con `httpx` directo a la Bot API: `send` abre un mensaje en el grupo y
devuelve su `message_id`; `edit` lo reescribe (al cerrar, con `resolved_text`).
Si Telegram falla se loguea y se sigue: nunca lanza, así el loop no se frena.

El agrupado por mesa y guardar el `message_id` en el `Alert` son de `processor.py`.
"""

import logging
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol

import httpx

from servio_vision import settings

logger = logging.getLogger(__name__)
# httpx loguea cada request en INFO con la URL completa, y la URL trae el token.
logging.getLogger("httpx").setLevel(logging.WARNING)

API_URL = "https://api.telegram.org"
RESOLVED_SUFFIX = "\n\n✅ ya resuelto"


def resolved_text(text: str) -> str:
    """Texto del mensaje original con la marca de resuelto."""
    return text + RESOLVED_SUFFIX


# Botones de un mensaje: filas de (texto, callback_data).
Buttons = list[list[tuple[str, str]]]


class Notifier(Protocol):
    def send(self, text: str, buttons: Buttons | None = None) -> int | None:
        """Manda un mensaje al grupo; devuelve su `message_id` o None si falló."""
        ...

    def edit(self, message_id: int, text: str) -> bool:
        """Reescribe un mensaje ya mandado (y le saca los botones); False si falló."""
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
    ) -> None:
        self._token = token
        self._chat_id = chat_id
        self._client = client or httpx.Client(timeout=timeout)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "TelegramNotifier":
        """Token y grupo de `env`; sin `env`, de la configuración (settings.py)."""
        if env is None:
            cfg = settings.current()
            env = {
                "TELEGRAM_BOT_TOKEN": cfg.telegram_bot_token,
                "TELEGRAM_GROUP_CHAT_ID": cfg.telegram_group_chat_id,
            }
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
        return _message_id(self._call("sendMessage", payload))

    def edit(self, message_id: int, text: str) -> bool:
        payload = {"chat_id": self._chat_id, "message_id": message_id, "text": text}
        return self._call("editMessageText", payload) is not None

    # --- respuestas de los mozos (feedback.py) ---

    def ask_reply(self, text: str, reply_to: int) -> int | None:
        """Pide que respondan a este mensaje escribiendo (force_reply)."""
        payload = {
            "chat_id": self._chat_id,
            "text": text,
            "reply_to_message_id": reply_to,
            "reply_markup": {"force_reply": True, "selective": True},
        }
        return _message_id(self._call("sendMessage", payload))

    def answer_callback(self, callback_id: str, text: str) -> None:
        self._call(
            "answerCallbackQuery", {"callback_query_id": callback_id, "text": text}
        )

    def delete(self, message_id: int) -> None:
        self._call(
            "deleteMessage", {"chat_id": self._chat_id, "message_id": message_id}
        )

    def get_updates(self, offset: int | None, timeout: int = 20) -> list[dict]:
        """Toques de botones y mensajes nuevos (long polling); [] si falla."""
        payload: dict[str, Any] = {
            "timeout": timeout,
            "allowed_updates": ["callback_query", "message"],
        }
        if offset is not None:
            payload["offset"] = offset
        result = self._call("getUpdates", payload, timeout=timeout + 10)
        return result if isinstance(result, list) else []

    def is_group(self, chat_id: object) -> bool:
        return str(chat_id) == str(self._chat_id)

    def send_photo(self, path: Path, caption: str = "") -> int | None:
        """Manda una imagen al grupo (por ejemplo, las métricas)."""
        files = {"photo": (path.name, path.read_bytes(), "image/png")}
        data = {"chat_id": self._chat_id, "caption": caption}
        return _message_id(self._call("sendPhoto", data, files=files, timeout=60))

    def _call(
        self,
        method: str,
        payload: dict[str, Any],
        timeout: float | None = None,
        files: dict[str, Any] | None = None,
    ) -> Any:
        """POST a la Bot API; devuelve `result` o None si falló (ya logueado).
        Con `files`, va como formulario (multipart) en vez de JSON."""
        url = f"{API_URL}/bot{self._token}/{method}"
        body: dict[str, Any] = (
            {"data": payload, "files": files} if files else {"json": payload}
        )
        if timeout is not None:
            body["timeout"] = timeout
        try:
            response = self._client.post(url, **body)
            data = response.json()
        except (httpx.HTTPError, ValueError) as error:
            # Sin el mensaje de httpx: trae la URL, y la URL trae el token.
            logger.warning("Telegram %s falló: %s", method, type(error).__name__)
            return None
        if not isinstance(data, dict) or not data.get("ok"):
            description = data.get("description") if isinstance(data, dict) else None
            logger.warning(
                "Telegram %s falló (HTTP %s): %s",
                method,
                response.status_code,
                description,
            )
            return None
        return data.get("result")


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
