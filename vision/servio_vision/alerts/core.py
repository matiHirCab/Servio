"""Núcleo de los avisos (A-4): estado de mesas, reglas y avisos abiertos, sin E/S.

`AlertCore.apply(obs)` actualiza la mesa (`state.py`), corre el motor (`engine.py`),
arma los avisos con su texto final (`messages.py`, con el mozo y el plato si hay
datos del restaurante) y devuelve lo que pasó como eventos, en orden. No manda, no
escribe log ni base: eso lo hacen los suscriptores (`outputs.py`), que reciben cada
evento. Así el núcleo se prueba con fixtures solo, sin archivos ni red.

Eventos:
- `TableUpdated`: la mesa después de la lectura (y si cambió algo que usan las reglas);
- `AlertsOpened`: avisos nuevos que van en un mensaje (los de una mesa juntos; el de
  plato lleno con datos del restaurante, solo y con la pregunta «¿qué pasó?»);
- `AlertsReminded`: recordatorios de una mesa, en un mensaje;
- `AlertsResolved`: avisos cerrados en esta lectura, con el motivo.

El `message_id` de cada aviso lo pone el canal: `attach` lo guarda en el aviso abierto.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime

from servio_vision.alerts import messages
from servio_vision.alerts.config import AlertConfig
from servio_vision.alerts.engine import ABSENT_READINGS_TO_CLOSE, evaluate
from servio_vision.alerts.models import Alert, TableObservation
from servio_vision.alerts.state import (
    TableState,
    empty_table,
    table_status,
    update_table,
)
from servio_vision.restaurant import Restaurant


@dataclass(frozen=True)
class TableUpdated:
    obs: TableObservation
    table: TableState
    # cambió algo que usan las reglas (no solo `last_seen`): vale guardarlo ya
    rules_changed: bool


@dataclass(frozen=True)
class Ask:
    """Datos para preguntar «¿qué pasó?» con botones (plato lleno)."""

    dish: str | None
    waiter: str | None
    answers: tuple[str, ...]
    plate: str | None  # track_id del plato lleno


@dataclass(frozen=True)
class AlertsOpened:
    table_id: str
    ts: datetime
    alerts: tuple[Alert, ...]
    text: str  # el mensaje entero
    ask: Ask | None = None


@dataclass(frozen=True)
class AlertsReminded:
    table_id: str
    ts: datetime
    alerts: tuple[Alert, ...]  # copias con reminders_sent + 1
    text: str


@dataclass(frozen=True)
class AlertsResolved:
    table_id: str
    ts: datetime
    alerts: tuple[Alert, ...]  # copias con status="resuelta"
    # mesa_terminada: "retirados" (mesa libre) o "servido" (llegó algo nuevo)
    reasons: Mapping[str, str | None]
    waiter: str | None
    # el texto con el que quedó el mensaje de cada aviso (lo pone el canal)
    texts: Mapping[str, str | None] = field(default_factory=dict)


Event = TableUpdated | AlertsOpened | AlertsReminded | AlertsResolved


class AlertCore:
    """Mesas y avisos abiertos en memoria."""

    def __init__(self, config: AlertConfig, restaurant: Restaurant | None = None):
        self.config = config
        # Con los datos del restaurante, los avisos nombran el plato y el mozo, y el de
        # plato lleno pregunta qué pasó (`Ask`).
        self.restaurant = restaurant
        self.tables: dict[str, TableState] = {}
        self.open_alerts: dict[str, Alert] = {}
        # mesa → lo que usaron las reglas en la última lectura (`_rules_view`): se
        # compara con la nueva sin recalcular la anterior
        self._views: dict[str, tuple] = {}

    def apply(self, obs: TableObservation) -> list[Event]:
        """Aplica una observación; `ValueError` si su ts va hacia atrás."""
        prev = self.tables.get(obs.table_id) or empty_table(obs.table_id)
        table = update_table(prev, obs)
        self.tables[obs.table_id] = table
        if self.restaurant is not None:
            self.restaurant.see_plates(
                obs.table_id, [p.track_id for p in table.active_plates()]
            )
        view = _rules_view(table)
        changed = self._views.get(obs.table_id) != view
        self._views[obs.table_id] = view
        events: list[Event] = [TableUpdated(obs, table, changed)]

        result = evaluate(table, obs.ts, self.config, self.open_alerts.values())
        grouped: list[Alert] = []
        asked: list[tuple[Alert, Ask]] = []
        for alert in result.opened:
            alert, ask = self._with_context(alert, table, obs.ts)
            self.open_alerts[alert.id] = alert
            if ask is None:
                grouped.append(alert)
            else:
                asked.append((alert, ask))
        if grouped:
            text = "\n".join(a.message for a in grouped)
            events.append(AlertsOpened(obs.table_id, obs.ts, tuple(grouped), text))
        for alert, ask in asked:
            events.append(
                AlertsOpened(obs.table_id, obs.ts, (alert,), alert.message, ask)
            )

        if result.reminded:
            waiter = self._waiter(obs.table_id)
            text = "\n".join(
                messages.mention_waiter(r.message, obs.table_id, waiter)
                for r in result.reminded
            )
            reminded = tuple(r.alert for r in result.reminded)
            for alert in reminded:
                self.open_alerts[alert.id] = alert
            events.append(AlertsReminded(obs.table_id, obs.ts, reminded, text))

        if result.resolved:
            reasons = {a.id: _close_reason(a, table) for a in result.resolved}
            for alert in result.resolved:
                del self.open_alerts[alert.id]
            events.append(
                AlertsResolved(
                    obs.table_id,
                    obs.ts,
                    tuple(result.resolved),
                    reasons,
                    self._waiter(obs.table_id),
                )
            )
        return events

    def forget(self, alerts: tuple[Alert, ...]) -> None:
        """Avisos recién abiertos que el canal no pudo mandar (lanzó): se sacan,
        así la próxima lectura los abre y los manda de nuevo."""
        for alert in alerts:
            self.open_alerts.pop(alert.id, None)

    def attach(self, alerts: tuple[Alert, ...]) -> None:
        """Los avisos abiertos, ya con el `message_id` que les dio el canal."""
        for alert in alerts:
            if alert.id in self.open_alerts:
                self.open_alerts[alert.id] = alert

    def _waiter(self, table_id: str) -> str | None:
        return self.restaurant.waiter(table_id) if self.restaurant else None

    def _with_context(
        self, alert: Alert, table: TableState, now: datetime
    ) -> tuple[Alert, Ask | None]:
        """Aviso con el plato y el mozo (si hay datos del restaurante). El de plato
        lleno vuelve con la pregunta para el mozo."""
        if self.restaurant is None:
            return alert, None
        waiter = self.restaurant.waiter(alert.table_id)
        if alert.type != "plato_lleno_mucho":
            message = messages.mention_waiter(alert.message, alert.table_id, waiter)
            return alert.model_copy(update={"message": message}), None
        full = sorted(
            (p for p in table.active_plates() if p.state == "lleno"),
            key=lambda p: p.first_seen,
        )
        plate = full[0] if full else None
        dish = self.restaurant.dish(alert.table_id, plate.track_id) if plate else None
        elapsed = now - plate.first_seen if plate else None
        message = messages.full_plate_question(
            alert.table_id, waiter, dish.name if dish else None, elapsed
        )
        ask = Ask(
            dish=dish.name if dish else None,
            waiter=waiter,
            answers=dish.answers if dish else messages.DEFAULT_ANSWERS,
            plate=plate.track_id if plate else None,
        )
        return alert.model_copy(update={"message": message}), ask


def _close_reason(alert: Alert, table: TableState) -> str | None:
    """Por qué se cerró un aviso de mesa terminada: "retirados" (la mesa quedó
    libre) o "servido" (llegó algo nuevo)."""
    if alert.type != "mesa_terminada":
        return None
    if table_status(p.state for p in table.active_plates()) != "libre":
        return "servido"
    return "retirados"


def _rules_view(table: TableState) -> tuple:
    """Lo de la mesa que usan las reglas: cada plato con todos sus campos menos
    `last_seen` (cambia en cada lectura y nadie lo mira), y de la mesa si hay gente.

    `last_ts` sí lo usan (un plato que apareció en la lectura anterior puede
    heredar los tiempos de uno retirado, `state._inherit_from_removed`), pero cambia
    en cada lectura: en su lugar va qué platos aparecieron en esta. La lectura
    siguiente los deja de contar, cambia la vista y se guarda: el `last_ts` guardado
    nunca deja «reciente» a un plato que ya no lo es. Las lecturas sin gente cuentan
    hasta el tope que mira el motor: pasado eso, una más no cambia ningún aviso."""
    plates = frozenset(
        tuple(v for k, v in vars(p).items() if k != "last_seen")
        for p in table.plates.values()
    )
    new = frozenset(t for t, p in table.plates.items() if p.id_since == table.last_ts)
    absent = min(table.people_absent_readings, ABSENT_READINGS_TO_CLOSE)
    return plates, new, table.people_present, absent
