"""Arma el núcleo y sus salidas (A-4). Ver docs/context/05-reglas-alertas.md.

`AlertProcessor.process(obs)` pasa la observación al núcleo (`core.py`: estado,
reglas, avisos abiertos, sin E/S) y reparte cada evento que devuelve a los
suscriptores (`outputs.py`), en este orden: el canal (`TelegramOutput`, que le pone
el `message_id` a los avisos), el log `out/eventos.jsonl` y la base. Una salida que
falla no frena a las demás. Las respuestas
de los mozos y los comandos los escucha el bot (`bot.py`).

El log empieza con una línea `run_started` con los umbrales de la corrida.

Estado que sobrevive a un reinicio (A-5): con base, lo que el núcleo y las salidas
tienen en memoria se guarda en `estado_servicio` (`persist.py`) y, con `resume`, se
recupera al arrancar si es reciente.
"""

import dataclasses
import functools
import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from servio_vision.alerts import persist
from servio_vision.alerts.bot import Bot, BotChannel, send_metrics
from servio_vision.alerts.config import AlertConfig
from servio_vision.alerts.core import AlertCore, AlertsOpened, TableUpdated
from servio_vision.alerts.feedback import FeedbackDesk
from servio_vision.alerts.models import Alert, TableObservation
from servio_vision.alerts.notify import Notifier
from servio_vision.alerts.outputs import (
    DatabaseOutput,
    EventLog,
    EventLogOutput,
    ObservationReceived,
    Subscriber,
    TelegramOutput,
)
from servio_vision.alerts.state import TableState
from servio_vision.db import Database
from servio_vision.restaurant import Restaurant
from servio_vision.timestamps import iso

logger = logging.getLogger(__name__)

# Un estado guardado hace más que esto ya no describe la sala: se arranca de cero.
RESUME_MAX_AGE_S = 600.0
# El estado se guarda cada tanto y no en cada lectura (una vuelta por mesa, con la
# base remota son idas y vueltas del loop). Se guarda al instante si se abre o cierra
# un aviso o si aparece un plato. Si cambia algo que usan las reglas (el estado de un
# plato, si hay gente), a lo sumo una vez por `SAVE_RULES_EVERY_S`: un corte brusco
# pierde como mucho ese último segundo de cambios, y una sala llena no escribe en cada
# lectura. Si no, cada `SAVE_STATE_EVERY_S`. `close()` guarda lo último.
SAVE_STATE_EVERY_S = 5.0
SAVE_RULES_EVERY_S = 1.0
# Al cerrar, cuánto se espera a que el hilo del bot termine lo que está haciendo.
BOT_STOP_WAIT_S = 2.0


class AlertProcessor:
    """El núcleo de los avisos con sus salidas: canal, log, base y bot."""

    def __init__(
        self,
        config: AlertConfig,
        notifier: Notifier,
        log_path: Path,
        restaurant: Restaurant | None = None,
        db: Database | None = None,
        resume: bool = False,
        resume_max_age_s: float = RESUME_MAX_AGE_S,
    ) -> None:
        """`resume`: seguir con el estado que dejó la corrida anterior en `db` (si es
        de hace menos de `resume_max_age_s`). Solo para una cámara en vivo: un video
        o un fixture empiezan siempre de cero."""
        self.config = config
        self.notifier = notifier
        self.restaurant = restaurant
        self.db = db  # base relacional (db.py): platos servidos, avisos y respuestas
        self.core = AlertCore(config, restaurant)
        self.log = EventLog(log_path)
        self.log_output = EventLogOutput(self.log)
        self.db_output = DatabaseOutput(db, restaurant) if db is not None else None
        if db is not None and restaurant is not None:
            db.load_restaurant(restaurant)
        # Con los datos del restaurante, el aviso de plato lleno pregunta qué pasó
        # (botones en Telegram, ver feedback.py) y el bot escucha las respuestas.
        self.feedback: FeedbackDesk | None = None
        self.bot: Bot | None = None
        if restaurant is not None:
            self.feedback = FeedbackDesk(notifier, self._on_feedback)
            # la consola no se escucha: sin canal que escuchar no hay bot
            if isinstance(notifier, BotChannel):
                commands: dict[str, Callable[[], None]] = {}
                if db is not None:
                    commands["metricas"] = functools.partial(
                        send_metrics, db, restaurant, notifier
                    )
                self.bot = Bot(notifier, self.feedback, commands)
        self.telegram = TelegramOutput(notifier, self.feedback)
        # Marca dónde empieza la corrida (el log se agrega) y con qué umbrales: replay.py
        # corta el archivo acá y reproduce con la misma config.
        self.log.write(
            {
                "event": "run_started",
                "started_at": iso(datetime.now(UTC)),
                "config": dataclasses.asdict(config),
            }
        )
        self._state_dirty = False  # hay estado sin guardar
        self._rules_dirty = False  # cambió algo de las reglas desde el último guardado
        self._save_failed = False  # el último guardado falló
        self._clock = time.monotonic  # los tests lo adelantan
        self._saved_at = float("-inf")  # `_clock()` del último intento
        if resume and db is not None:
            self._resume(db, resume_max_age_s)
        # al final: si algo de arriba falla, no queda un hilo huérfano leyendo Telegram
        if self.bot is not None:
            self.bot.start()

    @property
    def tables(self) -> dict[str, TableState]:
        return self.core.tables

    @property
    def open_alerts(self) -> dict[str, Alert]:
        return self.core.open_alerts

    def process(self, obs: TableObservation) -> list[dict[str, Any]]:
        """Procesa una observación de mesa; devuelve los eventos que escribió al log.

        La observación va al log antes de aplicarla (queda aunque el núcleo la
        rechace) y cada aviso apenas se manda o edita.
        """
        written = self._dispatch(ObservationReceived(obs))
        events = self.core.apply(obs)  # ValueError si el ts va hacia atrás
        for event in events:
            written += self._dispatch(event)
        if self.db_output is not None:
            self._state_dirty = True
            update = events[0]
            assert isinstance(update, TableUpdated)
            self._rules_dirty = self._rules_dirty or update.rules_changed
            since = self._clock() - self._saved_at
            if (
                len(events) > 1  # se abrió, recordó o cerró un aviso
                or self.db_output.save_now
                or (self._rules_dirty and since >= SAVE_RULES_EVERY_S)
                or since >= SAVE_STATE_EVERY_S
            ):
                self._flush_state()
        return written

    def _dispatch(self, event: Any) -> list[dict[str, Any]]:
        """Un evento a las salidas, en orden: el canal, que le pone el `message_id`
        a los avisos (el núcleo, el log y la base lo tienen que ver ya con él), el log
        y la base. Devuelve las líneas que llegaron al log.

        Si una salida falla se loguea y siguen las otras: el núcleo ya cambió su
        estado y un evento que no llega a nadie no se repite (un aviso cerrado
        quedaría sin editar). Salvo un aviso que el canal no pudo ni intentar mandar
        (lanzó): el núcleo lo olvida y se abre de nuevo en la próxima lectura. Si
        Telegram contesta que no (`send` devuelve None), el aviso queda abierto sin
        mensaje, como siempre."""
        event, sent = self._deliver(self.telegram, event)
        if isinstance(event, AlertsOpened):
            if not sent:
                self.core.forget(event.alerts)
                return []
            self.core.attach(event.alerts)  # ya con su message_id
        written: list[dict[str, Any]] = []
        try:
            written = self.log_output.write(event)
        except Exception:
            logger.exception("No se pudo escribir %s en el log", type(event).__name__)
        if self.db_output is not None:
            self._deliver(self.db_output, event)
        return written

    @staticmethod
    def _deliver(output: Subscriber, event: Any) -> tuple[Any, bool]:
        """El evento como lo deja la salida, y si anduvo."""
        try:
            return output.handle(event), True
        except Exception:
            logger.exception(
                "%s falló con %s", type(output).__name__, type(event).__name__
            )
            return event, False

    def close(self) -> None:
        if self.db_output is not None and self._state_dirty:
            self._flush_state()
        if self.bot is not None:
            # antes de cerrar lo que usa: una respuesta o un /metricas en curso
            self.bot.stop(wait_s=BOT_STOP_WAIT_S)
        # el de Telegram tiene un hilo con la cola de lo que no llegó (notify.py)
        close_notifier = getattr(self.notifier, "close", None)
        if close_notifier is not None:
            close_notifier()
        self.log.close()
        if self.db is not None:
            self.db.close()

    # --- respuestas y comandos (bot.py) ---

    def _on_feedback(self, event: dict[str, Any]) -> None:
        """Respuesta de un mozo: al log y a la base (si una falla, la otra igual)."""
        try:
            self.log.write(event)
        except Exception:
            logger.exception("No se pudo escribir la respuesta en el log")
        if self.db_output is not None:
            self.db_output.feedback(event)

    # --- estado que sobrevive a un reinicio (A-5) ---

    def _resume(self, db: Database, max_age_s: float) -> None:
        """Sigue con las mesas y los avisos abiertos de la corrida anterior: los
        tiempos de los platos no se reinician, un aviso abierto no se repite y su
        mensaje de Telegram se sigue pudiendo editar."""
        try:
            saved = db.load_state()
            if saved is None:
                return
            text, saved_at = saved
            age = (datetime.now(UTC) - saved_at).total_seconds()
            if age > max_age_s:
                logger.info("El estado guardado es de hace %d s: arranco de cero", age)
                return
            snapshot = persist.loads(text)
        except Exception:
            # ilegible o la base caída: arrancar de cero es lo que pasaba antes
            logger.exception("No se pudo recuperar el estado guardado: arranco de cero")
            return
        self.core.tables = snapshot.tables
        self.core.open_alerts = snapshot.open_alerts
        self.telegram.by_message = snapshot.messages
        self.telegram.resolved_ids = snapshot.resolved_ids
        self.telegram.close_notes = snapshot.close_notes
        if self.db_output is not None:
            self.db_output.served_rows = snapshot.served_rows
            self.db_output.plate_states = snapshot.plate_states
            self.db_output.on_table = snapshot.on_table
        logger.info(
            "Sigo donde quedé hace %d s: %d mesas, %d avisos abiertos",
            age,
            len(self.core.tables),
            len(self.core.open_alerts),
        )

    def _save_state(self) -> None:
        assert self.db is not None and self.db_output is not None
        snapshot = persist.Snapshot(
            tables=self.core.tables,
            open_alerts=self.core.open_alerts,
            messages=self.telegram.by_message,
            resolved_ids=self.telegram.resolved_ids,
            close_notes=self.telegram.close_notes,
            served_rows=self.db_output.served_rows,
            plate_states=self.db_output.plate_states,
            on_table=self.db_output.on_table,
        )
        self.db.save_state(persist.dumps(snapshot), datetime.now(UTC))
        self._state_dirty = False

    def _flush_state(self) -> None:
        assert self.db_output is not None  # hay base
        self._saved_at = self._clock()  # también si falla: no insistir cada lectura
        self.db_output.save_now = False
        # Si falla, un cambio de reglas que estaba esperando se reintenta una vez en
        # 1 s (un parpadeo de la base); si vuelve a fallar, la base está caída y se
        # sigue con el guardado de cada 5 s, sin una espera de conexión por segundo.
        rules_waiting = self._rules_dirty
        saved = self.db_output.write("el estado", self._save_state)
        self._rules_dirty = rules_waiting and not saved and not self._save_failed
        self._save_failed = not saved
