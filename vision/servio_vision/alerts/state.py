"""Estado por plato y estado de mesa (MAT-2). Ver docs/context/05-reglas-alertas.md.

Funciones puras: `update_table(prev, obs)` devuelve un `TableState` nuevo sin tocar
`prev`. Los platos se guardan por su `track_id` actual.

Cambio de id («Estado por plato» en 05). El suavizado, cuando el clasificador cambia
`p1 → p4`, manda los dos juntos una lectura y después solo `p4`. Entonces:
- Un id nuevo en la `position` de un plato que sigue en la mesa es **provisional** en esa
  lectura: `active_plates()` no lo cuenta, así no cambia el estado de la mesa ni
  dispara avisos.
- Cuando un plato deja de venir (retirado) y hay un **único** plato reciente (apareció
  en esta lectura o en la anterior) en su misma `position`, ese plato **hereda**
  `first_seen` (y `state_since` si tiene el mismo estado).
- Solo hereda un plato que apareció en esa `position` y cuyo estado es el mismo o un
  paso hacia adelante. Un retroceso o un salto (`vacio → lleno`: el postre que reemplaza
  al principal) no puede ser el mismo plato.
- Si viejo y nuevo siguen viniendo, eran dos platos: el nuevo cuenta desde la lectura
  siguiente, con sus propios tiempos.
- Un plato que no viene ya está retirado: el suavizado lo saca después de 2 lecturas
  ausente (03-contratos.md).
"""

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Literal

from servio_vision.alerts.models import TableObservation
from servio_vision.schemas import PlateState
from servio_vision.smoothing import is_hard_change

TableStatus = Literal["libre", "servida", "comiendo", "por_terminar", "terminada"]


@dataclass(frozen=True)
class PlateTrack:
    """Un plato de la mesa, con los tiempos que usan las reglas."""

    track_id: str
    state: PlateState
    position: str
    first_seen: datetime  # puede venir heredado de un id anterior
    state_since: datetime  # idem, si el estado es el mismo
    last_seen: datetime
    id_since: datetime  # cuándo apareció este track_id
    id_position: str  # en qué posición apareció este track_id
    provisional: bool = False  # id nuevo en la posición de un plato que sigue
    inherited_from: str | None = None  # track_id del que heredó los tiempos


@dataclass(frozen=True)
class TableState:
    """Platos de una mesa después de la última observación."""

    table_id: str
    plates: dict[str, PlateTrack] = field(default_factory=dict)
    last_ts: datetime | None = None

    def active_plates(self) -> list[PlateTrack]:
        """Platos que cuentan para las reglas (sin los provisionales)."""
        return [p for p in self.plates.values() if not p.provisional]


def empty_table(table_id: str) -> TableState:
    return TableState(table_id=table_id)


def update_table(prev: TableState, obs: TableObservation) -> TableState:
    """Aplica una observación de mesa y devuelve el estado nuevo."""
    if obs.table_id != prev.table_id:
        raise ValueError(f"observación de {obs.table_id} para la mesa {prev.table_id}")
    if prev.last_ts is not None and obs.ts < prev.last_ts:
        raise ValueError(f"ts {obs.ts} anterior a la última lectura {prev.last_ts}")
    ts = obs.ts

    plates: dict[str, PlateTrack] = {}
    for item in obs.items:
        old = prev.plates.get(item.track_id)
        if old is None:
            plates[item.track_id] = PlateTrack(
                track_id=item.track_id,
                state=item.state,
                position=item.position,
                first_seen=ts,
                state_since=ts,
                last_seen=ts,
                id_since=ts,
                id_position=item.position,
            )
        else:
            plates[item.track_id] = replace(
                old,
                state=item.state,
                position=item.position,
                state_since=old.state_since if item.state == old.state else ts,
                last_seen=ts,
            )

    _inherit_from_removed(prev, plates, ts)
    _mark_provisional(plates, ts)
    return TableState(prev.table_id, plates, ts)


def _inherit_from_removed(
    prev: TableState, plates: dict[str, PlateTrack], ts: datetime
) -> None:
    """Un retirado pasa sus tiempos al único plato reciente en su misma posición."""
    since = prev.last_ts or ts  # "reciente": apareció en esta lectura o en la anterior
    removed = [p for tid, p in prev.plates.items() if tid not in plates]
    removed_per_position = Counter(p.position for p in removed)
    for old in removed:
        if removed_per_position[old.position] != 1:
            continue  # dos retirados en la misma posición: no se sabe cuál es cuál
        candidates = [
            p
            for p in plates.values()
            if p.position == p.id_position == old.position
            and p.id_since >= since
            and p.inherited_from is None
            and (p.state == old.state or not is_hard_change(old.state, p.state))
        ]
        if len(candidates) != 1:
            continue
        new = candidates[0]
        plates[new.track_id] = replace(
            new,
            first_seen=old.first_seen,
            state_since=old.state_since if new.state == old.state else new.state_since,
            provisional=False,
            inherited_from=old.track_id,
        )


def _mark_provisional(plates: dict[str, PlateTrack], ts: datetime) -> None:
    """Id nuevo en esta lectura en la posición de un plato que sigue: provisional."""
    staying = {p.position for p in plates.values() if p.id_since < ts}
    for tid, p in plates.items():
        provisional = (
            p.id_since == ts and p.inherited_from is None and p.position in staying
        )
        if provisional != p.provisional:
            plates[tid] = replace(p, provisional=provisional)


def table_status(states: Iterable[PlateState]) -> TableStatus:
    """Estado de la mesa según los estados de sus platos («Estado de mesa» en 05)."""
    counts = Counter(states)
    total = sum(counts.values())
    if total == 0:
        return "libre"
    if counts["vacio"] == total:
        return "terminada"
    if (counts["por_terminar"] + counts["vacio"]) * 2 >= total:
        return "por_terminar"
    if counts["lleno"] * 2 >= total:
        return "servida"
    return "comiendo"
