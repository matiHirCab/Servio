"""Motor de reglas (MAT-3, C-5). Ver docs/context/05-reglas-alertas.md.

`evaluate(table, now, config, open_alerts, muted)` es pura: con el estado de una mesa
decide qué avisos abrir, cuáles recordar, cuáles escalar y cuáles cerrar. No manda
nada: lo usa el núcleo (`core.py`), y los textos salen de `messages.py`.

Por cada `(mesa, tipo)` hay como máximo un aviso abierto:
- condición sí y no hay aviso abierto → se abre (salvo que esté silenciado);
- condición no y hay uno abierto → se cierra (`resuelta`); el cierre gana siempre;
- condición sí y hay uno abierto, según lo que respondió el mozo (C-5):
  - dijo «Listo» (`done_at`): a `done_at + done_wait` se insiste una vez
    (`nudged_at`) y a `nudged_at + R` se escala;
  - dijo «Voy» (`answered_by`): nada más, los recordatorios se frenan;
  - nadie respondió: recordatorio a `created_at + R × n` hasta `max_reminders`, y
    cuando habría salido el siguiente (`created_at + R × (max_reminders + 1)`) se
    escala. Un aviso escala una sola vez (`escalated_at`).

`muted` son los silencios de «No hacía falta»: platos `(tipo, track_id)` que no
cuentan para ese tipo, y si la mesa entera no abre `mesa_terminada`.
"""

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from servio_vision.alerts.config import AlertConfig
from servio_vision.alerts.messages import (
    done_escalation_message,
    escalation_message,
    nudge_message,
    open_message,
    reminder_message,
)
from servio_vision.alerts.models import Alert, AlertType
from servio_vision.alerts.state import PlateTrack, TableState, table_status

# Lecturas seguidas sin gente para cerrar plato_lleno_mucho (05, «people_present»).
ABSENT_READINGS_TO_CLOSE = 2

ALERT_TYPES: tuple[AlertType, ...] = (
    "mesa_terminada",
    "plato_vacio",
    "plato_lleno_mucho",
)


@dataclass(frozen=True)
class Muted:
    """Silencios de una mesa tras «No hacía falta» (los guarda el núcleo)."""

    plates: frozenset[tuple[AlertType, str]] = frozenset()  # (tipo, track_id)
    table: bool = False  # mesa_terminada no vuelve a abrir hasta que deje de estarlo


NOT_MUTED = Muted()


@dataclass(frozen=True)
class Reminder:
    alert: Alert  # copia con reminders_sent + 1 (y nudged_at si es la insistencia)
    message: str


@dataclass(frozen=True)
class EngineResult:
    opened: list[Alert]
    reminded: list[Reminder]
    resolved: list[Alert]  # copias con status="resuelta"
    escalated: list[Reminder] = field(default_factory=list)  # copias con escalated_at


def evaluate(
    table: TableState,
    now: datetime,
    config: AlertConfig,
    open_alerts: Iterable[Alert],
    muted: Muted = NOT_MUTED,
) -> EngineResult:
    """Avisos a abrir, recordar, escalar y cerrar para una mesa en el instante `now`."""
    open_by_type = {
        a.type: a
        for a in open_alerts
        if a.table_id == table.table_id and a.status == "abierta"
    }
    opened: list[Alert] = []
    reminded: list[Reminder] = []
    resolved: list[Alert] = []
    escalated: list[Reminder] = []

    for alert_type in ALERT_TYPES:
        current = open_by_type.get(alert_type)
        since = condition_since(
            alert_type,
            table,
            now,
            config,
            already_open=current is not None,
            muted=muted,
        )
        if since is None:
            if current is not None:
                resolved.append(current.model_copy(update={"status": "resuelta"}))
            continue
        if current is None:
            opened.append(_new_alert(alert_type, table.table_id, now, since))
            continue
        followup = _follow_up(current, now, since, config)
        if followup is None:
            continue
        kind, reminder = followup
        (reminded if kind == "remind" else escalated).append(reminder)
    return EngineResult(opened, reminded, resolved, escalated)


def _follow_up(
    alert: Alert, now: datetime, since: datetime, config: AlertConfig
) -> tuple[str, Reminder] | None:
    """Qué toca para un aviso abierto cuya condición sigue: `("remind", …)`,
    `("escalate", …)` o nada."""
    if alert.escalated_at is not None:
        return None
    reminder = timedelta(seconds=config.reminder)
    if alert.done_at is not None:
        # «Listo»: una insistencia a los `done_wait` y, si sigue igual, escalado
        if alert.nudged_at is None:
            if now >= alert.done_at + timedelta(seconds=config.done_wait):
                return "remind", Reminder(
                    alert=alert.model_copy(
                        update={
                            "reminders_sent": alert.reminders_sent + 1,
                            "nudged_at": now,
                        }
                    ),
                    message=nudge_message(
                        alert.type, alert.table_id, now - alert.done_at
                    ),
                )
            return None
        if now >= alert.nudged_at + reminder:
            return "escalate", Reminder(
                alert=alert.model_copy(update={"escalated_at": now}),
                message=done_escalation_message(
                    alert.type, alert.table_id, now - alert.done_at, alert.answered_by
                ),
            )
        return None
    if alert.answered_by is not None:
        return None  # «Voy» frena los recordatorios
    if alert.reminders_sent < config.max_reminders:
        n = alert.reminders_sent + 1
        if now >= alert.created_at + reminder * n:
            return "remind", Reminder(
                alert=alert.model_copy(update={"reminders_sent": n}),
                message=reminder_message(alert.type, alert.table_id, now - since),
            )
        return None
    if now >= alert.created_at + reminder * (config.max_reminders + 1):
        return "escalate", _escalation(alert, now, since)
    return None


def _escalation(alert: Alert, now: datetime, since: datetime) -> Reminder:
    return Reminder(
        alert=alert.model_copy(update={"escalated_at": now}),
        message=escalation_message(
            alert.type,
            alert.table_id,
            now - since,
            unanswered=alert.reminders_sent + 1,  # el aviso y sus recordatorios
        ),
    )


def condition_since(
    alert_type: AlertType,
    table: TableState,
    now: datetime,
    config: AlertConfig,
    *,
    already_open: bool,
    muted: Muted = NOT_MUTED,
) -> datetime | None:
    """Desde cuándo se cumple la condición del aviso, o None si no se cumple."""
    plates = table.active_plates()
    status = table_status(p.state for p in plates)
    if alert_type == "mesa_terminada":
        # Todos los platos vacíos. Solo se cierra si retiran los platos (libre) o
        # sirven algo nuevo: pasar de por_terminar a terminada no "resuelve" nada.
        if status != "terminada":
            return None
        if muted.table and not already_open:
            return None  # «No hacía falta»: hasta que la mesa deje de estar terminada
        return max(p.state_since for p in plates)  # cuando se vació el último

    if alert_type == "plato_vacio":
        # Un plato vacío mientras el resto de la mesa sigue comiendo: retirarlo sin
        # esperar. Si la mesa entera terminó, avisa mesa_terminada. Ya abierto, sigue
        # hasta que retiran ese plato (no se "resuelve" porque terminaron los demás).
        if status == "terminada" and not already_open:
            return None
        times = [
            p.state_since
            for p in empty_plates(plates, now, config)
            if (alert_type, p.track_id) not in muted.plates
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
        for p in full_plates(plates, now, config)
        if (alert_type, p.track_id) not in muted.plates
    ]
    return min(times) if times else None


def empty_plates(
    plates: Iterable[PlateTrack], now: datetime, config: AlertConfig
) -> list[PlateTrack]:
    """Los platos en `vacio` hace ≥ T1 (los que cuentan para `plato_vacio`)."""
    return [
        p
        for p in plates
        if p.state == "vacio" and now - p.state_since >= timedelta(seconds=config.t1)
    ]


def full_plates(
    plates: Iterable[PlateTrack], now: datetime, config: AlertConfig
) -> list[PlateTrack]:
    """Los platos en `lleno` hace ≥ T2 (los que cuentan para `plato_lleno_mucho`)."""
    return [
        p
        for p in plates
        if p.state == "lleno" and now - p.first_seen >= timedelta(seconds=config.t2)
    ]


def _new_alert(
    alert_type: AlertType, table_id: str, now: datetime, since: datetime
) -> Alert:
    return Alert(
        id=f"{table_id}-{alert_type}-{int(now.timestamp())}",
        table_id=table_id,
        type=alert_type,
        message=open_message(alert_type, table_id, now - since),
        created_at=now,
        status="abierta",
    )
