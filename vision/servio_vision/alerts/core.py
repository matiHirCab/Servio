"""Núcleo de los avisos (A-4, C-5): estado de mesas, reglas y avisos abiertos, sin E/S.

`AlertCore.apply(obs)` actualiza la mesa (`state.py`), corre el motor (`engine.py`),
arma los avisos con su texto final (`messages.py`, con el mozo y el plato si hay
datos del restaurante) y devuelve lo que pasó como eventos, en orden. No manda, no
escribe log ni base: eso lo hacen los suscriptores (`outputs.py`), que reciben cada
evento. Así el núcleo se prueba con fixtures solo, sin archivos ni red.

`AlertCore.respond(alert_id, action, who)` es lo que el mozo contestó desde Telegram
(C-5): «Voy» toma el aviso y frena los recordatorios, «Listo» anota `done_at`, un
motivo (plato lleno) también lo toma, y «No hacía falta» es el único cierre a mano:
cierra con `no_hacia_falta` y silencia ese plato (o esa mesa) para que la cámara no
vuelva a abrir lo mismo.

Eventos:
- `TableUpdated`: la mesa después de la lectura (y si cambió algo que usan las reglas);
- `AlertsOpened`: un aviso nuevo, en su propio mensaje (el de plato lleno con datos
  del restaurante trae la pregunta «¿qué pasó?»);
- `AlertsReminded`: recordatorios de una mesa, en un mensaje;
- `AlertsEscalated`: avisos de una mesa que escalan (nadie respondió), en un mensaje;
- `AlertsResolved`: avisos cerrados en esta lectura (o por «No hacía falta»), con el motivo.

El `message_id` de cada aviso lo pone el canal: `attach` lo guarda en el aviso abierto.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from servio_vision.alerts import messages
from servio_vision.alerts.config import AlertConfig
from servio_vision.alerts.engine import (
    ABSENT_READINGS_TO_CLOSE,
    NOT_MUTED,
    Muted,
    empty_plates,
    evaluate,
    full_plates,
)
from servio_vision.alerts.models import Alert, AlertType, TableObservation
from servio_vision.alerts.state import (
    TableState,
    empty_table,
    table_status,
    update_table,
)
from servio_vision.restaurant import Restaurant

Action = Literal["voy", "listo", "no_hacia_falta", "motivo"]
ACTIONS: tuple[Action, ...] = ("voy", "listo", "no_hacia_falta", "motivo")
NO_HACIA_FALTA = "no_hacia_falta"  # motivo de cierre, junto a "retirados" y "servido"


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
    alerts: tuple[Alert, ...]  # uno por mensaje (C-5: cada aviso con sus botones)
    text: str  # el mensaje entero
    ask: Ask | None = None
    waiter: str | None = None


@dataclass(frozen=True)
class AlertsReminded:
    table_id: str
    ts: datetime
    alerts: tuple[Alert, ...]  # copias con reminders_sent + 1
    text: str


@dataclass(frozen=True)
class AlertsEscalated:
    table_id: str
    ts: datetime
    alerts: tuple[Alert, ...]  # copias con escalated_at
    text: str
    waiter: str | None


@dataclass(frozen=True)
class AlertsResolved:
    table_id: str
    ts: datetime
    alerts: tuple[Alert, ...]  # copias con status="resuelta"
    # mesa_terminada: "retirados" (mesa libre) o "servido" (llegó algo nuevo);
    # cualquier tipo: "no_hacia_falta" (lo cerró el mozo)
    reasons: Mapping[str, str | None]
    waiter: str | None
    # el texto con el que quedó el mensaje de cada aviso (lo pone el canal)
    texts: Mapping[str, str | None] = field(default_factory=dict)


Event = TableUpdated | AlertsOpened | AlertsReminded | AlertsEscalated | AlertsResolved


class AlertCore:
    """Mesas y avisos abiertos en memoria."""

    def __init__(self, config: AlertConfig, restaurant: Restaurant | None = None):
        self.config = config
        # Con los datos del restaurante, los avisos nombran el plato y el mozo, y el de
        # plato lleno pregunta qué pasó (`Ask`).
        self.restaurant = restaurant
        self.tables: dict[str, TableState] = {}
        self.open_alerts: dict[str, Alert] = {}
        # mesa → silencios de «No hacía falta» (engine.Muted)
        self.muted: dict[str, Muted] = {}
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
        muted = self._refresh_muted(table)
        view = _rules_view(table, muted)
        changed = self._views.get(obs.table_id) != view
        self._views[obs.table_id] = view
        events: list[Event] = [TableUpdated(obs, table, changed)]

        result = evaluate(table, obs.ts, self.config, self.open_alerts.values(), muted)
        for alert in result.opened:
            alert, ask = self._with_context(alert, table, obs.ts)
            self.open_alerts[alert.id] = alert
            events.append(
                AlertsOpened(
                    obs.table_id,
                    obs.ts,
                    (alert,),
                    alert.message,
                    ask,
                    self._waiter(obs.table_id),
                )
            )

        waiter = self._waiter(obs.table_id)
        if result.reminded:
            text = "\n".join(
                messages.mention_waiter(r.message, obs.table_id, waiter)
                for r in result.reminded
            )
            reminded = tuple(r.alert for r in result.reminded)
            for alert in reminded:
                self.open_alerts[alert.id] = alert
            events.append(AlertsReminded(obs.table_id, obs.ts, reminded, text))

        if result.escalated:
            text = "\n".join(
                messages.escalation_mention(r.message, waiter) for r in result.escalated
            )
            escalated = tuple(r.alert for r in result.escalated)
            for alert in escalated:
                self.open_alerts[alert.id] = alert
            events.append(
                AlertsEscalated(obs.table_id, obs.ts, escalated, text, waiter)
            )

        if result.resolved:
            reasons = {a.id: _close_reason(a, table) for a in result.resolved}
            for alert in result.resolved:
                del self.open_alerts[alert.id]
            events.append(
                AlertsResolved(
                    obs.table_id, obs.ts, tuple(result.resolved), reasons, waiter
                )
            )
        return events

    def respond(
        self, alert_id: str, action: Action, who: str, now: datetime | None = None
    ) -> list[Event] | None:
        """Lo que contestó el mozo sobre un aviso abierto. Devuelve los eventos que
        produce (solo «No hacía falta» cierra: `AlertsResolved`), `[]` si no cambió
        nada, o None si el aviso no está abierto. `now`: el reloj del motor; por
        defecto, la última lectura de la mesa."""
        alert = self.open_alerts.get(alert_id)
        if alert is None or alert.status != "abierta":
            return None
        table = self.tables.get(alert.table_id) or empty_table(alert.table_id)
        if now is None:
            now = table.last_ts or alert.created_at
        update: dict[str, object] = {}
        if action == "voy":
            if alert.answered_by is not None:
                return []
            update["answered_by"] = who
        elif action == "listo":
            if alert.done_at is not None:
                return []
            update["done_at"] = now
            if alert.answered_by is None:
                update["answered_by"] = who
        elif action == "motivo":
            if alert.answered_by is None:
                update["answered_by"] = who
        else:
            resolved = alert.model_copy(update={"status": "resuelta"})
            del self.open_alerts[alert_id]
            self._mute(resolved, table, now)
            return [
                AlertsResolved(
                    alert.table_id,
                    now,
                    (resolved,),
                    {alert.id: NO_HACIA_FALTA},
                    self._waiter(alert.table_id),
                )
            ]
        if update:
            self.open_alerts[alert_id] = alert.model_copy(update=update)
        return []

    def attach(self, alerts: tuple[Alert, ...]) -> None:
        """Los avisos abiertos, ya con el `message_id` que les dio el canal."""
        for alert in alerts:
            if alert.id in self.open_alerts:
                self.open_alerts[alert.id] = alert

    def _waiter(self, table_id: str) -> str | None:
        return self.restaurant.waiter(table_id) if self.restaurant else None

    # --- silencios de «No hacía falta» ---

    def _mute(self, alert: Alert, table: TableState, now: datetime) -> None:
        """Lo que la cámara ve mal lo va a seguir viendo: no volver a avisar por ese
        plato (plato vacío, plato lleno) o por esa mesa hasta que deje de estar
        terminada. Otro plato de la misma mesa sí tiene que avisar."""
        current = self.muted.get(alert.table_id, NOT_MUTED)
        plates = set(current.plates)
        if alert.type == "mesa_terminada":
            self.muted[alert.table_id] = Muted(frozenset(plates), table=True)
            return
        active = table.active_plates()
        guilty = (
            empty_plates(active, now, self.config)
            if alert.type == "plato_vacio"
            else full_plates(active, now, self.config)
        )
        plates |= {(alert.type, p.track_id) for p in guilty}
        self.muted[alert.table_id] = Muted(frozenset(plates), current.table)

    def _refresh_muted(self, table: TableState) -> Muted:
        """Los silencios de la mesa después de una lectura: siguen al plato si cambió
        de id (`inherited_from`), se levantan cuando el plato se va o cambia de
        estado, y el de la mesa cuando deja de estar terminada."""
        current = self.muted.get(table.table_id)
        if current is None:
            return NOT_MUTED
        states = {
            "plato_vacio": "vacio",
            "plato_lleno_mucho": "lleno",
        }
        plates: set[tuple[AlertType, str]] = set()
        for alert_type, track_id in current.plates:
            plate = table.plates.get(track_id)
            if plate is None:
                heirs = [
                    p for p in table.plates.values() if p.inherited_from == track_id
                ]
                plate = heirs[0] if len(heirs) == 1 else None
            if plate is not None and plate.state == states[alert_type]:
                plates.add((alert_type, plate.track_id))
        muted_table = current.table and (
            table_status(p.state for p in table.active_plates()) == "terminada"
        )
        if not plates and not muted_table:
            del self.muted[table.table_id]
            return NOT_MUTED
        refreshed = Muted(frozenset(plates), muted_table)
        self.muted[table.table_id] = refreshed
        return refreshed

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


# Campos de `PlateTrack` que usan las reglas: todos menos `last_seen` (cambia en cada
# lectura y nadie lo mira). Un test falla si `PlateTrack` suma un campo que no está
# acá ni en `IGNORED_PLATE_FIELDS`: así un cambio que importa no se escapa del guardado.
RULES_PLATE_FIELDS = (
    "track_id",
    "state",
    "position",
    "first_seen",
    "state_since",
    "id_since",
    "id_position",
    "provisional",
    "inherited_from",
)
IGNORED_PLATE_FIELDS = ("last_seen",)


def _rules_view(table: TableState, muted: Muted = NOT_MUTED) -> tuple:
    """Lo de la mesa que usan las reglas: cada plato con `RULES_PLATE_FIELDS`, de
    la mesa si hay gente, y los silencios.

    `last_ts` sí lo usan (un plato que apareció en la lectura anterior puede
    heredar los tiempos de uno retirado, `state._inherit_from_removed`), pero cambia
    en cada lectura: en su lugar va qué platos aparecieron en esta. La lectura
    siguiente los deja de contar, cambia la vista y se guarda: el `last_ts` guardado
    nunca deja «reciente» a un plato que ya no lo es. Las lecturas sin gente cuentan
    hasta el tope que mira el motor: pasado eso, una más no cambia ningún aviso."""
    plates = frozenset(
        tuple(getattr(p, name) for name in RULES_PLATE_FIELDS)
        for p in table.plates.values()
    )
    new = frozenset(t for t, p in table.plates.items() if p.id_since == table.last_ts)
    absent = min(table.people_absent_readings, ABSENT_READINGS_TO_CLOSE)
    return plates, new, table.people_present, absent, muted
