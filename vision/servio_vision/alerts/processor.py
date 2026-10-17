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
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from servio_vision.alerts.config import AlertConfig
from servio_vision.alerts.engine import evaluate
from servio_vision.alerts.models import Alert, TableObservation
from servio_vision.alerts.notify import Notifier, resolved_text
from servio_vision.alerts.state import TableState, empty_table, update_table

logger = logging.getLogger(__name__)

RESOLVED_LINE_MARK = " ✅"


class EventLog:
    """Una línea JSON por evento, agregada al final del archivo (no se pisa)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        # Abierto una sola vez; buffering=1 baja cada línea al disco al escribirla.
        self._file = path.open("a", encoding="utf-8", buffering=1)

    def write(self, event: dict[str, Any]) -> None:
        self._file.write(json.dumps(event, ensure_ascii=False) + "\n")

    def close(self) -> None:
        self._file.close()


class AlertProcessor:
    """Estado en memoria de todas las mesas y de los avisos abiertos."""

    def __init__(self, config: AlertConfig, notifier: Notifier, log_path: Path) -> None:
        self.config = config
        self.notifier = notifier
        self.log = EventLog(log_path)
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

        result = evaluate(table, obs.ts, self.config, self.open_alerts.values())

        if result.opened:
            text = "\n".join(a.message for a in result.opened)
            message_id = self.notifier.send(text)
            opened = [
                a.model_copy(update={"telegram_message_id": message_id})
                for a in result.opened
            ]
            if message_id is not None:
                self._messages[message_id] = opened
            for alert in opened:
                self.open_alerts[alert.id] = alert
                log(_alert_event("alert_opened", obs.ts, alert, text))

        if result.reminded:
            text = "\n".join(r.message for r in result.reminded)
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
        self.log.close()

    def _edit(self, message_id: int) -> str:
        """Reescribe un mensaje con sus avisos cerrados marcados; devuelve el texto."""
        alerts = self._messages[message_id]
        if all(a.id in self._resolved_ids for a in alerts):
            text = resolved_text("\n".join(a.message for a in alerts))
            del self._messages[message_id]
            self._resolved_ids -= {a.id for a in alerts}
        else:
            text = _partial_text(alerts, self._resolved_ids)
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
