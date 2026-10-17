"""Envío a Telegram (MAT-4). Ver docs/context/06-notificaciones.md.

Solo envío, con `httpx` directo a la Bot API: `send` abre un mensaje en el grupo y
devuelve su `message_id`; `edit` lo reescribe (al cerrar, con `resolved_text`).
Si Telegram falla se loguea y se sigue: nunca lanza, así el loop no se frena.

El agrupado por mesa y guardar el `message_id` en el `Alert` son de `processor.py`.
"""

import logging
import os
import sys
from collections.abc import Mapping
from typing import Any, Protocol

import httpx

logger = logging.getLogger(__name__)
# httpx loguea cada request en INFO con la URL completa, y la URL trae el token.
logging.getLogger("httpx").setLevel(logging.WARNING)

API_URL = "https://api.telegram.org"
RESOLVED_SUFFIX = "\n\n✅ ya resuelto"


def resolved_text(text: str) -> str:
    """Texto del mensaje original con la marca de resuelto."""
    return text + RESOLVED_SUFFIX


class Notifier(Protocol):
    def send(self, text: str) -> int | None:
        """Manda un mensaje al grupo; devuelve su `message_id` o None si falló."""
        ...

    def edit(self, message_id: int, text: str) -> bool:
        """Reescribe un mensaje ya mandado; False si falló."""
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
        env = os.environ if env is None else env
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

    def send(self, text: str) -> int | None:
        result = self._call("sendMessage", {"chat_id": self._chat_id, "text": text})
        if not isinstance(result, dict) or not isinstance(
            result.get("message_id"), int
        ):
            return None
        return result["message_id"]

    def edit(self, message_id: int, text: str) -> bool:
        payload = {"chat_id": self._chat_id, "message_id": message_id, "text": text}
        return self._call("editMessageText", payload) is not None

    def _call(self, method: str, payload: dict[str, Any]) -> Any:
        """POST a la Bot API; devuelve `result` o None si falló (ya logueado)."""
        url = f"{API_URL}/bot{self._token}/{method}"
        try:
            response = self._client.post(url, json=payload)
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

    def send(self, text: str) -> int:
        self._last_id += 1
        _print(f"[telegram #{self._last_id}] {text}")
        return self._last_id

    def edit(self, message_id: int, text: str) -> bool:
        _print(f"[telegram editado #{message_id}] {text}")
        return True


def _print(text: str) -> None:
    """Imprime sin fallar: en Windows, con stdout redirigido (cp1252), los emojis
    tiran `UnicodeEncodeError`; ahí salen como `?` en vez de cortar el aviso."""
    try:
        print(text, flush=True)
    except UnicodeEncodeError:
        encoding = sys.stdout.encoding or "utf-8"
        print(text.encode(encoding, "replace").decode(encoding), flush=True)


def get_notifier(telegram: bool, env: Mapping[str, str] | None = None) -> Notifier:
    """Telegram si se pidió y hay credenciales; si no, consola."""
    if not telegram:
        return ConsoleNotifier()
    try:
        return TelegramNotifier.from_env(env)
    except ValueError as error:
        logger.warning("%s: imprimo los avisos en consola", error)
        return ConsoleNotifier()
