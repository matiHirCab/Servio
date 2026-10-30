"""El bot del grupo de mozos (A-4): escucha el canal y reparte lo que llega.

Separado del notificador (`notify.py`, que solo manda y edita): el bot escucha
toques de botón, texto libre y comandos (`/metricas`). Las respuestas a los avisos
de plato lleno las registra `FeedbackDesk` (`feedback.py`); los comandos, las
funciones de `commands`. Del canal solo usa `BotChannel`, así cambiar Telegram por
otro canal (C-6) es otra implementación de ese protocolo.

Telegram se escucha con long polling (`getUpdates`) en un hilo aparte: no hace falta
servidor ni webhook. Con `--no-telegram` (ConsoleNotifier) no hay nada que escuchar.
"""

import logging
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from servio_vision import metrics
from servio_vision.alerts import messages
from servio_vision.alerts.feedback import FeedbackDesk
from servio_vision.db import Database
from servio_vision.restaurant import Restaurant

logger = logging.getLogger(__name__)

# Al arrancar sin red: espera entre intentos de saber desde dónde escuchar.
RETRY_START_S = 1.0
RETRY_START_MAX_S = 30.0


@runtime_checkable
class BotChannel(Protocol):
    """Lo que el bot necesita del canal: traer lo nuevo y saber si un mensaje es
    del grupo (lo implementa `TelegramNotifier`; la consola no se escucha). Lo que
    hace falta para contestar un botón lo usa `FeedbackDesk`."""

    def get_updates(
        self, offset: int | None, timeout: int = 20
    ) -> list[dict] | None: ...  # None si falló

    def is_group(self, chat_id: object) -> bool: ...


class Bot:
    """Escucha el canal en un hilo y reparte: comandos a `commands`, el resto a
    `desk` (respuestas de los mozos)."""

    def __init__(
        self,
        channel: BotChannel,
        desk: FeedbackDesk | None = None,
        commands: dict[str, Callable[[], None]] | None = None,
    ) -> None:
        self.channel = channel
        self.desk = desk
        # comandos del grupo ("/metricas" → commands["metricas"]())
        self.commands = commands or {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._listen, name="bot", daemon=True)
        self._thread.start()

    def stop(self, wait_s: float = 0.0) -> None:
        """Deja de escuchar y espera hasta `wait_s` a que el hilo termine lo que está
        haciendo (una respuesta, un `/metricas`). Una consulta larga al canal no se
        puede cortar, pero lo que traiga ya no se maneja."""
        self._stop.set()
        if wait_s and self._thread is not None:
            self._thread.join(timeout=wait_s)

    def _start_offset(self) -> int | None:
        """Desde dónde escuchar: después de lo que quedó pendiente de corridas
        anteriores. Si el canal no contesta, se insiste: sin saberlo, la primera
        lectura traería todo lo viejo (cada `/metricas` de ayer se repetiría)."""
        wait = RETRY_START_S
        while not self._stop.is_set():
            pending = self.channel.get_updates(offset=-1, timeout=0)
            if pending is not None:
                return pending[-1]["update_id"] + 1 if pending else None
            self._stop.wait(wait)
            wait = min(wait * 2, RETRY_START_MAX_S)
        return None

    def _listen(self) -> None:
        offset = self._start_offset()
        if self._stop.is_set():
            return
        logger.info("Escuchando respuestas de los mozos en Telegram")
        while not self._stop.is_set():
            updates = self.channel.get_updates(offset) or []  # None: falló, sigue
            for update in updates:
                if self._stop.is_set():
                    return  # ya se cerró: el log y la base no están
                offset = update["update_id"] + 1
                try:
                    self.handle(update)
                except Exception:
                    logger.exception("No se pudo procesar una respuesta de Telegram")
            if not updates:
                self._stop.wait(0.5)

    def handle(self, update: dict) -> None:
        message = update.get("message")
        if message is not None and self._command(message):
            return
        if self.desk is not None:
            self.desk.handle(update)

    def _command(self, message: dict) -> bool:
        """Corre el comando del mensaje, si lo es; True si era un comando."""
        text = (message.get("text") or "").strip()
        if not text.startswith("/"):
            return False
        if not self.channel.is_group(message.get("chat", {}).get("id")):
            return True
        # "/metricas" o "/metricas@servio_mozos_bot"
        name = text.split()[0][1:].split("@")[0].lower()
        command = self.commands.get(name)
        if command is not None:
            logger.info("Comando /%s desde Telegram", name)
            command()
        return True


def send_metrics(db: Database, restaurant: Restaurant, notifier: Any) -> None:
    """`/metricas` en el grupo: la imagen de métricas con un resumen corto.
    Junta el historial simulado (se importa una vez) con lo de hoy."""
    db.import_history(metrics.HISTORY, restaurant)
    m = metrics.compute(db, operation=False)  # la imagen no las usa
    if not m["dishes"]:
        notifier.send(messages.NO_METRICS)
        return
    image: Path = db.path.parent / "metricas.png"
    metrics.render(m, image)
    notifier.send_photo(image, metrics.summary(m))
    problems = metrics.problems_text(m)  # aparte: en el pie de foto no entra
    if problems:
        notifier.send(problems)
