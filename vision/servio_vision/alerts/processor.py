"""Une estado, motor y Telegram (MAT-5). Ver docs/context/05-reglas-alertas.md.

`AlertProcessor.process(obs)` actualiza el estado de la mesa (`state.py`), corre el
motor (`engine.py`) y manda, recuerda o edita con el notifier (`notify.py`). Cada
observación y cada aviso quedan en el log `out/eventos.jsonl` (03-contratos.md, «Log»),
después de una línea `run_started` con los umbrales de la corrida.

Agrupado por mesa: los avisos que se abren en la misma evaluación van en un solo
mensaje, y lo mismo los recordatorios. Al cerrar se edita el mensaje en el que se abrió
el aviso: si todos sus avisos están cerrados, con "✅ ya resuelto" al final; si no, solo
las líneas cerradas llevan "✅".
"""

import dataclasses
import json
import logging
import threading
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from servio_vision.alerts.config import AlertConfig
from servio_vision.alerts.engine import evaluate, table_name
from servio_vision.alerts.feedback import FeedbackDesk, Question
from servio_vision.alerts.models import Alert, TableObservation
from servio_vision.alerts.notify import Notifier, resolved_text
from servio_vision.alerts.state import TableState, empty_table, update_table
from servio_vision.restaurant import Restaurant

logger = logging.getLogger(__name__)

RESOLVED_LINE_MARK = " ✅"


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


class AlertProcessor:
    """Estado en memoria de todas las mesas y de los avisos abiertos."""

    def __init__(
        self,
        config: AlertConfig,
        notifier: Notifier,
        log_path: Path,
        restaurant: Restaurant | None = None,
    ) -> None:
        self.config = config
        self.notifier = notifier
        self.log = EventLog(log_path)
        # Con los datos del restaurante, los avisos nombran el plato y el mozo, y el de
        # plato lleno pregunta qué pasó (botones en Telegram, ver feedback.py).
        self.restaurant = restaurant
        self.feedback: FeedbackDesk | None = None
        if restaurant is not None:
            self.feedback = FeedbackDesk(notifier, self.log.write)
            self.feedback.start()
        # Marca dónde empieza la corrida (el log se agrega) y con qué umbrales: replay.py
        # corta el archivo acá y reproduce con la misma config.
        self.log.write(
            {
                "event": "run_started",
                "started_at": _iso(datetime.now(UTC)),
                "config": dataclasses.asdict(config),
            }
        )
        self.tables: dict[str, TableState] = {}
        self.open_alerts: dict[str, Alert] = {}
        # message_id → avisos de ese mensaje (en orden) y cuáles ya se cerraron.
        self._messages: dict[int, list[Alert]] = {}
        self._resolved_ids: set[str] = set()

    def process(self, obs: TableObservation) -> list[dict[str, Any]]:
        """Procesa una observación de mesa; devuelve los eventos que escribió al log.

        Cada evento se escribe apenas pasa: la observación antes de aplicarla (queda
        aunque `update_table` la rechace) y cada aviso apenas se manda o edita.
        """
        events: list[dict[str, Any]] = []

        def log(event: dict[str, Any]) -> None:
            self.log.write(event)
            events.append(event)

        log({"event": "observation", **obs.model_dump(mode="json")})
        prev = self.tables.get(obs.table_id) or empty_table(obs.table_id)
        table = update_table(prev, obs)  # ValueError si el ts va hacia atrás
        self.tables[obs.table_id] = table

        if self.restaurant is not None:
            self.restaurant.see_plates(
                obs.table_id, [p.track_id for p in table.active_plates()]
            )

        result = evaluate(table, obs.ts, self.config, self.open_alerts.values())
        opened_all = [self._with_context(a, table, obs.ts) for a in result.opened]
        grouped = [a for a, question in opened_all if question is None]

        if grouped:
            text = "\n".join(a.message for a in grouped)
            message_id = self.notifier.send(text)
            opened = [
                a.model_copy(update={"telegram_message_id": message_id})
                for a in grouped
            ]
            if message_id is not None:
                self._messages[message_id] = opened
            for alert in opened:
                self.open_alerts[alert.id] = alert
                log(_alert_event("alert_opened", obs.ts, alert, text))

        for alert, question in opened_all:
            if question is None:
                continue
            # plato lleno: mensaje propio, con los botones de "¿qué pasó?"
            message_id = self.feedback.ask(question)
            alert = alert.model_copy(update={"telegram_message_id": message_id})
            if message_id is not None:
                self._messages[message_id] = [alert]
            self.open_alerts[alert.id] = alert
            log(_alert_event("alert_opened", obs.ts, alert, alert.message))

        if result.reminded:
            text = "\n".join(
                self._mention_waiter(r.message, obs.table_id) for r in result.reminded
            )
            self.notifier.send(text)  # si falla, ya se logueó; el aviso sigue igual
            for reminder in result.reminded:
                self.open_alerts[reminder.alert.id] = reminder.alert
                log(_alert_event("alert_reminder", obs.ts, reminder.alert, text))

        # Primero se marcan todos los cerrados, así un mensaje se edita una sola vez.
        message_ids: list[int] = []
        for alert in result.resolved:
            del self.open_alerts[alert.id]
            message_id = alert.telegram_message_id
            if message_id is not None:
                self._resolved_ids.add(alert.id)
                if message_id not in message_ids:
                    message_ids.append(message_id)
        edited = {message_id: self._edit(message_id) for message_id in message_ids}
        for alert in result.resolved:
            # Sin mensaje (Telegram falló al abrir) no hubo nada que editar: text None.
            text = edited.get(alert.telegram_message_id)
            log(_alert_event("alert_resolved", obs.ts, alert, text))

        return events

    def close(self) -> None:
        if self.feedback is not None:
            self.feedback.stop()
        self.log.close()

    def _with_context(
        self, alert: Alert, table: TableState, now: datetime
    ) -> tuple[Alert, Question | None]:
        """Aviso con el plato y el mozo (si hay datos del restaurante). El de plato
        lleno vuelve con la pregunta para el mozo."""
        if self.restaurant is None:
            return alert, None
        waiter = self.restaurant.waiter(alert.table_id)
        if alert.type != "plato_lleno_mucho" or self.feedback is None:
            message = self._mention_waiter(alert.message, alert.table_id)
            return alert.model_copy(update={"message": message}), None
        full = sorted(
            (p for p in table.active_plates() if p.state == "lleno"),
            key=lambda p: p.first_seen,
        )
        plate = full[0] if full else None
        dish = self.restaurant.dish(alert.table_id, plate.track_id) if plate else None
        minutes = (
            max(1, round((now - plate.first_seen).total_seconds() / 60)) if plate else 1
        )
        who = f" · {waiter}" if waiter else ""
        what = dish.name if dish else "un plato"
        message = (
            f"❓ {table_name(alert.table_id)}{who}: {what} sigue lleno hace "
            f"{minutes} min. ¿Está todo bien? Pasá a preguntar y contanos qué pasó."
        )
        alert = alert.model_copy(update={"message": message})
        question = Question(
            alert_id=alert.id,
            table_id=alert.table_id,
            alert_type=alert.type,
            dish=dish.name if dish else None,
            waiter=waiter,
            text=message,
            answers=dish.answers if dish else ("Está todo bien", "No le gustó"),
            asked_at=datetime.now(UTC),
        )
        return alert, question

    def _mention_waiter(self, message: str, table_id: str) -> str:
        """`Mesa 1 terminó…` → `Mesa 1 · Juan terminó…`."""
        waiter = self.restaurant.waiter(table_id) if self.restaurant else None
        if not waiter:
            return message
        name = table_name(table_id)
        return message.replace(name, f"{name} · {waiter}", 1)

    def _edit(self, message_id: int) -> str:
        """Reescribe un mensaje con sus avisos cerrados marcados; devuelve el texto."""
        alerts = self._messages[message_id]
        # las respuestas del mozo quedan en el mensaje (antes del "ya resuelto")
        answers = self.feedback.suffix(message_id) if self.feedback else ""
        if all(a.id in self._resolved_ids for a in alerts):
            text = resolved_text("\n".join(a.message for a in alerts) + answers)
            del self._messages[message_id]
            self._resolved_ids -= {a.id for a in alerts}
        else:
            text = _partial_text(alerts, self._resolved_ids) + answers
        self.notifier.edit(message_id, text)  # si falla, ya se logueó
        return text


def _partial_text(alerts: Sequence[Alert], resolved_ids: set[str]) -> str:
    return "\n".join(
        a.message + (RESOLVED_LINE_MARK if a.id in resolved_ids else "") for a in alerts
    )


def _alert_event(
    name: str, ts: datetime, alert: Alert, text: str | None
) -> dict[str, Any]:
    return {
        "event": name,
        "ts": _iso(ts),
        **alert.model_dump(mode="json"),
        "text": text,
    }


def _iso(ts: datetime) -> str:
    # Mismo formato que pydantic usa para los demás ts del log (UTC con "Z").
    return ts.isoformat().replace("+00:00", "Z")
