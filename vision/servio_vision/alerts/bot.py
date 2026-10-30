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
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from servio_vision import metrics
from servio_vision.alerts import messages
from servio_vision.alerts.feedback import FeedbackDesk
from servio_vision.db import Database
from servio_vision.restaurant import Restaurant

logger = logging.getLogger(__name__)

# Al arrancar sin red, un mensaje de más de esto antes del arranque es de una
# corrida anterior (margen por si el reloj de esta máquina va adelantado).
OLD_MESSAGE_S = 600.0


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
        # tomado mientras se maneja un update: `stop` espera solo a eso, no a la
        # consulta larga al canal (que no se puede cortar)
        self._busy = threading.Lock()
        self._started_at = time.time()
        # arrancó sin red: se lee todo lo pendiente y se saltean los mensajes viejos
        self._catching_up = False

    def start(self) -> None:
        self._started_at = time.time()
        self._thread = threading.Thread(target=self._listen, name="bot", daemon=True)
        self._thread.start()

    def stop(self, wait_s: float = 0.0) -> None:
        """Deja de escuchar. Si el hilo está manejando algo (una respuesta, un
        `/metricas`), espera hasta `wait_s` a que termine; lo que traiga después la
        consulta en curso ya no se maneja."""
        self._stop.set()
        if wait_s and self._busy.acquire(timeout=wait_s):
            self._busy.release()

    def _start_offset(self) -> int | None:
        """Desde dónde escuchar: después de lo que quedó pendiente de corridas
        anteriores. Sin respuesta del canal no se sabe; se lee desde lo más viejo y
        `_is_old` saltea los mensajes de antes del arranque (cada `/metricas` de ayer
        mandaría la imagen otra vez). Los toques de botón sí se manejan: uno de un
        aviso viejo contesta que ya no está activo, y uno de un aviso de esta corrida
        (llegó mientras no había red) se registra."""
        pending = self.channel.get_updates(offset=-1, timeout=0)
        if pending is None:
            logger.warning("Telegram no contesta: salteo los mensajes viejos")
            self._catching_up = True
            return None
        return pending[-1]["update_id"] + 1 if pending else None

    def _is_old(self, update: dict) -> bool:
        """Solo si arrancó sin red: un mensaje (texto o comando) de bastante antes del
        arranque. El margen es porque `date` es la hora de Telegram y `_started_at`
        la de esta máquina, que puede ir adelantada: una respuesta nueva no se pierde,
        y los `/metricas` que importan saltear son de horas antes."""
        message = update.get("message")
        if not self._catching_up or message is None:
            return False
        return message.get("date", time.time()) < self._started_at - OLD_MESSAGE_S

    def _listen(self) -> None:
        offset = self._start_offset()
        logger.info("Escuchando respuestas de los mozos en Telegram")
        while not self._stop.is_set():
            updates = self.channel.get_updates(offset) or []  # None: falló, sigue
            for update in updates:
                with self._busy:
                    if self._stop.is_set():
                        return  # ya se cerró: el log y la base no están
                    offset = update["update_id"] + 1
                    if self._is_old(update):
                        continue
                    try:
                        self.handle(update)
                    except Exception:
                        logger.exception(
                            "No se pudo procesar una respuesta de Telegram"
                        )
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
