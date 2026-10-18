"""Motor de reglas (MAT-3). Ver docs/context/05-reglas-alertas.md.

`evaluate(table, now, config, open_alerts)` es pura: con el estado de una mesa decide qué
avisos abrir, cuáles recordar y cuáles cerrar. No manda nada; eso lo hace `processor.py`
(`alerts.process()`), que además agrupa en un solo mensaje los avisos de la misma mesa.

Por cada `(mesa, tipo)` hay como máximo un aviso abierto:
- condición sí y no hay aviso abierto → se abre;
- condición sí y hay uno abierto → recordatorio a `created_at + R × n` (máximo 2);
- condición no y hay uno abierto → se cierra (`resuelta`).
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

from servio_vision.alerts.config import AlertConfig
from servio_vision.alerts.models import Alert, AlertType
from servio_vision.alerts.state import TableState, table_status

# Lecturas seguidas sin gente para cerrar plato_lleno_mucho (05, «people_present»).
ABSENT_READINGS_TO_CLOSE = 2

ALERT_TYPES: tuple[AlertType, ...] = (
    "mesa_terminada",
    "plato_vacio",
    "plato_lleno_mucho",
)


@dataclass(frozen=True)
class Reminder:
    alert: Alert  # copia con reminders_sent + 1
    message: str


@dataclass(frozen=True)
class EngineResult:
    opened: list[Alert]
    reminded: list[Reminder]
    resolved: list[Alert]  # copias con status="resuelta"


def evaluate(
    table: TableState,
    now: datetime,
    config: AlertConfig,
    open_alerts: Iterable[Alert],
) -> EngineResult:
    """Avisos a abrir, recordar y cerrar para una mesa en el instante `now`."""
    open_by_type = {
        a.type: a
        for a in open_alerts
        if a.table_id == table.table_id and a.status == "abierta"
    }
    opened: list[Alert] = []
    reminded: list[Reminder] = []
    resolved: list[Alert] = []

    for alert_type in ALERT_TYPES:
        current = open_by_type.get(alert_type)
        since = _condition_since(
            alert_type, table, now, config, already_open=current is not None
        )
        if since is None:
            if current is not None:
                resolved.append(current.model_copy(update={"status": "resuelta"}))
            continue
        if current is None:
            opened.append(_new_alert(alert_type, table.table_id, now, since))
            continue
        n = current.reminders_sent + 1
        due = current.created_at + timedelta(seconds=config.reminder * n)
        if current.reminders_sent < config.max_reminders and now >= due:
            reminded.append(
                Reminder(
                    alert=current.model_copy(update={"reminders_sent": n}),
                    message=_reminder_message(alert_type, table.table_id, now - since),
                )
            )
    return EngineResult(opened, reminded, resolved)


def _condition_since(
    alert_type: AlertType,
    table: TableState,
    now: datetime,
    config: AlertConfig,
    *,
    already_open: bool,
) -> datetime | None:
    """Desde cuándo se cumple la condición del aviso, o None si no se cumple."""
    plates = table.active_plates()
    status = table_status(p.state for p in plates)
    if alert_type == "mesa_terminada":
        # Todos los platos vacíos. Solo se cierra si retiran los platos (libre) o
        # sirven algo nuevo: pasar de por_terminar a terminada no "resuelve" nada.
        if status != "terminada":
            return None
        return max(p.state_since for p in plates)  # cuando se vació el último

    if alert_type == "plato_vacio":
        # Un plato vacío mientras el resto de la mesa sigue comiendo: retirarlo sin
        # esperar. Si la mesa entera terminó, avisa mesa_terminada. Ya abierto, sigue
        # hasta que retiran ese plato (no se "resuelve" porque terminaron los demás).
        if status == "terminada" and not already_open:
            return None
        times = [
            p.state_since
            for p in plates
            if p.state == "vacio"
            and now - p.state_since >= timedelta(seconds=config.t1)
        ]
        return min(times) if times else None

    # plato_lleno_mucho: abre con gente; ya abierto, solo lo cierran 2 lecturas sin gente.
    if already_open:
        if table.people_absent_readings >= ABSENT_READINGS_TO_CLOSE:
            return None
    elif not table.people_present:
        return None
    times = [
        p.first_seen
        for p in plates
        if p.state == "lleno" and now - p.first_seen >= timedelta(seconds=config.t2)
    ]
    return min(times) if times else None


def _new_alert(
    alert_type: AlertType, table_id: str, now: datetime, since: datetime
) -> Alert:
    return Alert(
        id=f"{table_id}-{alert_type}-{int(now.timestamp())}",
        table_id=table_id,
        type=alert_type,
        message=_open_message(alert_type, table_id, now - since),
        created_at=now,
        status="abierta",
    )


def table_name(table_id: str) -> str:
    """Nombre para el mensaje: `T1` → `Mesa 1`; cualquier otro id queda igual."""
    match = re.fullmatch(r"T(\d+)", table_id)
    return f"Mesa {match.group(1)}" if match else table_id


def _minutes(elapsed: timedelta) -> int:
    return max(1, round(elapsed.total_seconds() / 60))


def _open_message(alert_type: AlertType, table_id: str, elapsed: timedelta) -> str:
    name = table_name(table_id)
    if alert_type == "mesa_terminada":
        return f"🍽️ {name} terminó: retirá los platos y ofrecé postre, café o la cuenta."
    if alert_type == "plato_vacio":
        return f"🧹 {name}: hay platos vacíos para retirar."
    return f"❓ {name}: un plato sigue lleno hace {_minutes(elapsed)} min. ¿Está todo bien?"


def _reminder_message(alert_type: AlertType, table_id: str, elapsed: timedelta) -> str:
    name = table_name(table_id)
    minutes = _minutes(elapsed)
    if alert_type == "mesa_terminada":
        return f"🍽️ {name} terminó hace {minutes} min y los platos siguen en la mesa."
    if alert_type == "plato_vacio":
        return f"🧹 {name}: los platos vacíos siguen sin retirar hace {minutes} min."
    return f"❓ {name}: el plato sigue lleno hace {minutes} min. ¿Está todo bien?"
