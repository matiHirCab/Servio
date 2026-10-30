"""Suscriptores de los eventos del núcleo (A-4): canal, log y base.

Cada uno recibe los eventos de `core.py` (y `ObservationReceived`, la lectura tal
como llegó) con `handle(event)` y devuelve el evento, quizá completado: el canal le
pone el `message_id` a los avisos abiertos y el texto final a los cerrados, y los
que vienen después (log, base) ya los ven. `processor.py` los llama en ese orden.

- `TelegramOutput`: agrupa en un mensaje, manda, recuerda y edita al cerrar (vía
  `Notifier`, cualquier canal). Al cerrar edita el mensaje en el que se abrió el
  aviso: si todos sus avisos están cerrados, con "✅ ya resuelto" al final; si no,
  solo las líneas cerradas llevan "✅".
- `EventLogOutput`: una línea por evento en `out/eventos.jsonl` (03-contratos.md, «Log»).
- `DatabaseOutput`: platos servidos, lecturas, avisos y respuestas en `servio.db`.
"""

import json
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from servio_vision.alerts import messages
from servio_vision.alerts.core import (
    AlertsOpened,
    AlertsReminded,
    AlertsResolved,
    Event,
    TableUpdated,
)
from servio_vision.alerts.feedback import FeedbackDesk, Question
from servio_vision.alerts.models import Alert, TableObservation
from servio_vision.alerts.notify import Notifier
from servio_vision.alerts.state import TableState
from servio_vision.db import Database
from servio_vision.restaurant import Restaurant

logger = logging.getLogger(__name__)

# Una base caída falla en cada lectura: el traceback se loguea una vez por minuto.
DB_LOG_EVERY_S = 60.0
# Un plato que dejó de estar en su mesa conserva su fila de `platos_servidos`
# (`served_rows`) tantas lecturas más, por si el mismo id vuelve (lo taparon): si no,
# se lo insertaría de nuevo. Pasado esto el id ya no vuelve (`IdCounter` solo recuerda
# unas pocas lecturas y los ids solo suben) y se olvida: la foto que se guarda en cada
# guardado no puede crecer con cada plato que se sirvió en semanas.
PRUNE_SERVED_AFTER_READINGS = 30


@dataclass(frozen=True)
class ObservationReceived:
    """La lectura tal como llegó, antes de aplicarla (queda en el log aunque el
    núcleo la rechace)."""

    obs: TableObservation


AnyEvent = Event | ObservationReceived


class Subscriber(Protocol):
    def handle(self, event: AnyEvent) -> AnyEvent: ...


# --- canal (Telegram o la consola) ---


class TelegramOutput:
    """Manda los avisos y edita sus mensajes al cerrarse."""

    def __init__(self, notifier: Notifier, desk: FeedbackDesk | None = None) -> None:
        self.notifier = notifier
        self.desk = desk  # con él, el de plato lleno lleva botones
        # message_id → avisos de ese mensaje (en orden) y cuáles ya se cerraron.
        self.messages: dict[int, list[Alert]] = {}
        self.resolved_ids: set[str] = set()
        # aviso de mesa terminada cerrado porque retiraron los platos → la línea
        # que reemplaza al "ya resuelto" (quién y cuánto tardó)
        self.close_notes: dict[str, str] = {}

    def handle(self, event: AnyEvent) -> AnyEvent:
        if isinstance(event, AlertsOpened):
            return self._opened(event)
        if isinstance(event, AlertsReminded):
            self.notifier.send(event.text)  # si falla, ya se logueó; el aviso sigue
        elif isinstance(event, AlertsResolved):
            return self._resolved(event)
        return event

    def _opened(self, event: AlertsOpened) -> AlertsOpened:
        if event.ask is not None and self.desk is not None:
            (alert,) = event.alerts
            # plato lleno: mensaje propio, con los botones de "¿qué pasó?"
            message_id = self.desk.ask(
                Question(
                    alert_id=alert.id,
                    table_id=alert.table_id,
                    alert_type=alert.type,
                    dish=event.ask.dish,
                    waiter=event.ask.waiter,
                    text=event.text,
                    answers=event.ask.answers,
                    asked_at=datetime.now(UTC),
                )
            )
        else:
            message_id = self.notifier.send(event.text)
        alerts = tuple(
            a.model_copy(update={"telegram_message_id": message_id})
            for a in event.alerts
        )
        if message_id is not None:
            self.messages[message_id] = list(alerts)
        return replace(event, alerts=alerts)

    def _resolved(self, event: AlertsResolved) -> AlertsResolved:
        # Primero se marcan todos los cerrados, así un mensaje se edita una sola vez.
        message_ids: list[int] = []
        for alert in event.alerts:
            message_id = alert.telegram_message_id
            if message_id is None:
                continue
            if event.reasons.get(alert.id) == "retirados":
                # solo si hay un mensaje que editar: `_edit` es quien la usa
                self.close_notes[alert.id] = messages.picked_up_note(
                    event.waiter, event.ts - alert.created_at
                )
            self.resolved_ids.add(alert.id)
            if message_id not in message_ids:
                message_ids.append(message_id)
        edited = {message_id: self._edit(message_id) for message_id in message_ids}
        # Sin mensaje (Telegram falló al abrir) no hubo nada que editar: text None.
        texts = {
            a.id: edited.get(a.telegram_message_id)
            if a.telegram_message_id is not None
            else None
            for a in event.alerts
        }
        return replace(event, texts=texts)

    def _edit(self, message_id: int) -> str:
        """Reescribe un mensaje con sus avisos cerrados marcados; devuelve el texto."""
        alerts = self.messages[message_id]
        done = all(a.id in self.resolved_ids for a in alerts)
        resolved_ids = set(self.resolved_ids)
        notes = [self.close_notes.pop(a.id) for a in alerts if a.id in self.close_notes]

        def build(answers: str) -> str:
            # las respuestas del mozo quedan en el mensaje (antes del "ya resuelto")
            text = "\n".join(a.message for a in alerts) + answers
            if done and notes:
                # mesa terminada y platos retirados: quién y cuánto tardó
                return text + "\n\n" + "\n".join(notes)
            if done:
                return messages.resolved_text(text)
            return messages.partial_text(alerts, resolved_ids) + answers

        if done:
            del self.messages[message_id]
            self.resolved_ids -= {a.id for a in alerts}
        if self.desk is not None:
            # con el lock de las respuestas: una que llega a la vez no lo pisa
            return self.desk.rewrite(message_id, build, resolved=done)
        text = build("")
        self.notifier.edit(message_id, text)  # si falla, ya se logueó
        return text


# --- log ---


class EventLog:
    """Una línea JSON por evento, agregada al final del archivo (no se pisa)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        # Abierto una sola vez; buffering=1 baja cada línea al disco al escribirla.
        self._file = path.open("a", encoding="utf-8", buffering=1)
        self._lock = threading.Lock()  # también escriben las respuestas de los mozos

    def write(self, event: dict[str, Any]) -> None:
        with self._lock:
            self._file.write(json.dumps(event, ensure_ascii=False) + "\n")

    def close(self) -> None:
        self._file.close()


class EventLogOutput:
    """Cada evento, a `EventLog` con el formato del log (03-contratos.md)."""

    def __init__(self, log: EventLog) -> None:
        self.log = log
        self.written: list[dict[str, Any]] = []  # lo escrito (la fachada lo vacía)

    def write(self, line: dict[str, Any]) -> None:
        self.log.write(line)
        self.written.append(line)

    def handle(self, event: AnyEvent) -> AnyEvent:
        if isinstance(event, ObservationReceived):
            self.write({"event": "observation", **event.obs.model_dump(mode="json")})
        elif isinstance(event, AlertsOpened):
            for alert in event.alerts:
                self.write(alert_event("alert_opened", event.ts, alert, event.text))
        elif isinstance(event, AlertsReminded):
            for alert in event.alerts:
                self.write(alert_event("alert_reminder", event.ts, alert, event.text))
        elif isinstance(event, AlertsResolved):
            for alert in event.alerts:
                line = alert_event(
                    "alert_resolved", event.ts, alert, event.texts.get(alert.id)
                )
                if event.reasons.get(alert.id):
                    line["close_reason"] = event.reasons[alert.id]
                self.write(line)
        return event


def alert_event(name: str, ts: datetime, alert: Alert, text: str | None) -> dict:
    return {
        "event": name,
        "ts": iso(ts),
        **alert.model_dump(mode="json"),
        "text": text,
    }


def iso(ts: datetime) -> str:
    # Mismo formato que pydantic usa para los demás ts del log (UTC con "Z").
    return ts.isoformat().replace("+00:00", "Z")


# --- base ---


class DatabaseOutput:
    """Platos servidos, lecturas, avisos y respuestas, a la base (`db.py`).

    La base es auxiliar (métricas e historial): si una escritura falla se loguea y
    se sigue, así el aviso al mozo sale igual."""

    def __init__(self, db: Database, restaurant: Restaurant | None = None) -> None:
        self.db = db
        self.restaurant = restaurant
        self.served_rows: dict[str, int] = {}  # track_id → platos_servidos.id
        self.plate_states: dict[str, str] = {}  # track_id → último estado guardado
        self.on_table: dict[str, set[str]] = {}  # mesa → track_ids en la base
        # track_id → lecturas seguidas de su mesa sin él (no se guarda: al reanudar
        # vuelve a contar, y solo se olvida más tarde)
        self._absent: dict[str, int] = {}
        # pasó algo que no puede esperar al próximo guardado del estado
        self.save_now = False
        # (escritura, tipo de error) → cuándo se logueó, y cuántos fallos se callaron
        self._logged: dict[tuple[str, str], float] = {}
        self._muted: dict[tuple[str, str], int] = {}

    def handle(self, event: AnyEvent) -> AnyEvent:
        if isinstance(event, TableUpdated):
            obs, table = event.obs, event.table
            self.write("las lecturas", self._record_plates, obs, table, obs.ts)
            self._prune_served(obs.table_id, table)
        elif isinstance(event, AlertsOpened):
            for alert in event.alerts:
                plate = event.ask.plate if event.ask is not None else None
                served_id = self.served_rows.get(plate) if plate else None
                self.write(
                    "el aviso abierto",
                    self.db.alert_opened,
                    alert.id,
                    alert.type,
                    alert.table_id,
                    served_id,
                    event.ts,
                )
        elif isinstance(event, AlertsResolved):
            for alert in event.alerts:
                self.write(
                    "el aviso cerrado",
                    self.db.alert_resolved,
                    alert.id,
                    event.ts,
                    event.reasons.get(alert.id),
                )
        return event

    def feedback(self, event: dict[str, Any]) -> None:
        """Respuesta de un mozo (`alert_feedback`)."""
        try:
            self.db.feedback(event)
        except Exception:
            logger.exception("No se pudo guardar la respuesta en la base")

    def write(self, what: str, write: Callable[..., Any], *args: Any) -> None:
        """Una escritura a la base. Con la base caída falla en cada lectura: el
        traceback sale una vez por `DB_LOG_EVERY_S` y los demás se cuentan."""
        try:
            write(*args)
        except Exception as error:
            # por escritura y por tipo de error: una causa nueva no se calla
            key = (what, type(error).__name__)
            now = time.monotonic()
            last = self._logged.get(key)
            if last is not None and now - last < DB_LOG_EVERY_S:
                self._muted[key] = self._muted.get(key, 0) + 1
                return
            self._logged[key] = now
            muted = self._muted.pop(key, 0)
            more = f" (y {muted} fallos más desde el último aviso)" if muted else ""
            logger.exception("No se pudo guardar %s en la base%s", what, more)

    def _record_plates(
        self, obs: TableObservation, table: TableState, ts: datetime
    ) -> None:
        """Cada plato nuevo de la mesa, a la base (con el plato del pedido que es),
        y la lectura de cada plato: estado, cuánto le queda y si se fue de la
        mesa (métricas de M-1). Un plato que heredó los tiempos de otro (cambió
        de id) sigue siendo el mismo plato servido."""
        if self.restaurant is None:
            return
        active = table.active_plates()
        for plate in active:
            if plate.track_id in self.served_rows:
                continue
            # el id de la fila tiene que llegar al estado guardado ya: si no, tras
            # un reinicio el plato se insertaría de nuevo
            self.save_now = True
            dish = self.restaurant.dish(obs.table_id, plate.track_id)
            inherited_from = plate.inherited_from
            same = self.served_rows.get(inherited_from or "")
            if same is not None and inherited_from is not None:
                self.served_rows[plate.track_id] = same
                last = self.plate_states.pop(inherited_from, None)
                if last is not None:
                    self.plate_states[plate.track_id] = last
                continue
            self.served_rows[plate.track_id] = self.db.served(
                obs.table_id, dish.dish_id if dish else None, plate.track_id, ts
            )
        readings = []
        states: dict[str, str] = {}
        for item in obs.items:
            served_id = self.served_rows.get(item.track_id)
            if served_id is None:
                continue
            changed = self.plate_states.get(item.track_id) != item.state
            states[item.track_id] = item.state
            readings.append((served_id, item.state, item.fill_ratio, changed))
        # los que estaban y ya no están (salvo que otro id haya heredado el plato)
        inherited = {p.inherited_from for p in table.plates.values()}
        before = self.on_table.get(obs.table_id, set())
        gone = before - set(table.plates) - inherited
        self.db.plate_readings(
            readings,
            [self.served_rows[t] for t in sorted(gone) if t in self.served_rows],
            ts,
        )
        # Recién con la base al día: si la escritura falló, la próxima lectura
        # repite estos cambios de estado y estos platos retirados en vez de perderlos.
        self.plate_states.update(states)
        for track_id in gone:
            self.plate_states.pop(track_id, None)
        self.on_table[obs.table_id] = {p.track_id for p in active}

    def _prune_served(self, table_id: str, table: TableState) -> None:
        """Olvida las filas de los platos que llevan `PRUNE_SERVED_AFTER_READINGS`
        lecturas seguidas fuera de su mesa y cuyo retiro ya llegó a la base.

        Es memoria y anda aunque la base esté caída, pero una fila no se olvida
        mientras su plato siga en `on_table`: eso quiere decir que el retiro todavía
        no se pudo registrar (`on_table` se actualiza recién cuando la base escribió),
        y sin la fila se perdería."""
        awaiting_retirement = self.on_table.get(table_id, set())
        for track_id in [t for t in self.served_rows if _table_of(t) == table_id]:
            if track_id in table.plates:
                self._absent.pop(track_id, None)
                continue
            absent = self._absent[track_id] = self._absent.get(track_id, 0) + 1
            if (
                absent >= PRUNE_SERVED_AFTER_READINGS
                and track_id not in awaiting_retirement
            ):
                del self.served_rows[track_id]
                del self._absent[track_id]


def _table_of(track_id: str) -> str:
    """`T1-p3` → `T1`; `mesa-1-p3` → `mesa-1`. El id de un plato es `{mesa}-{id}` y el
    del plato no lleva guion (`p3`); cortar por el último guion no confunde una mesa
    con otra que empieza igual (`mesa` y `mesa-1`), a diferencia de un prefijo."""
    return track_id.rsplit("-", 1)[0]
