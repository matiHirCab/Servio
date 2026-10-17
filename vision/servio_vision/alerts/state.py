"""Estado por plato y estado de mesa (MAT-2). Ver docs/context/05-reglas-alertas.md.

Funciones puras: `update_table(prev, obs)` devuelve un `TableState` nuevo sin tocar
`prev`.

Reconciliación de ids («Estado por plato» en 05):
- Un `track_id` nuevo en la misma `position` que un único plato que ya estaba en la
  mesa es ese mismo plato: se guarda el alias (nuevo → canónico) y no se reinician
  `first_seen` ni `state_since`. Pasa venga o no el id viejo en esa lectura, porque el
  suavizado retiene una lectura al viejo y manda los dos juntos.
- Si hay más de un candidato (dos platos con esa `position`, o dos ids nuevos), no se
  empareja: el id nuevo es un plato nuevo.
- Un plato que no viene en la observación ya está retirado: el suavizado lo saca
  después de 2 lecturas ausente (03-contratos.md).
"""

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Literal

from servio_vision.alerts.models import ObservedPlate, TableObservation
from servio_vision.schemas import PlateState

TableStatus = Literal["libre", "servida", "comiendo", "por_terminar", "terminada"]


@dataclass(frozen=True)
class PlateTrack:
    """Un plato de la mesa, con los tiempos que usan las reglas."""

    track_id: str  # canónico: el primer id con el que lo vimos
    state: PlateState
    position: str
    first_seen: datetime
    state_since: datetime
    last_seen: datetime


@dataclass(frozen=True)
class TableState:
    """Platos de una mesa después de la última observación."""

    table_id: str
    plates: dict[str, PlateTrack] = field(default_factory=dict)  # por id canónico
    aliases: dict[str, str] = field(default_factory=dict)  # id de la visión → canónico
    last_ts: datetime | None = None


def empty_table(table_id: str) -> TableState:
    return TableState(table_id=table_id)


def update_table(prev: TableState, obs: TableObservation) -> TableState:
    """Aplica una observación de mesa y devuelve el estado nuevo."""
    if obs.table_id != prev.table_id:
        raise ValueError(f"observación de {obs.table_id} para la mesa {prev.table_id}")
    if prev.last_ts is not None and obs.ts < prev.last_ts:
        raise ValueError(f"ts {obs.ts} anterior a la última lectura {prev.last_ts}")

    aliases = dict(prev.aliases)
    readings = _resolve(prev, obs.items, aliases)

    plates: dict[str, PlateTrack] = {}
    for canonical, item in readings.items():
        old = prev.plates.get(canonical)
        if old is None:
            plates[canonical] = PlateTrack(
                track_id=canonical,
                state=item.state,
                position=item.position,
                first_seen=obs.ts,
                state_since=obs.ts,
                last_seen=obs.ts,
            )
        else:
            plates[canonical] = replace(
                old,
                state=item.state,
                position=item.position,
                state_since=old.state_since if item.state == old.state else obs.ts,
                last_seen=obs.ts,
            )

    # Lo que no vino ya está retirado: se va el plato y sus alias.
    aliases = {new: canon for new, canon in aliases.items() if canon in plates}
    return TableState(prev.table_id, plates, aliases, obs.ts)


def _resolve(
    prev: TableState, items: list[ObservedPlate], aliases: dict[str, str]
) -> dict[str, ObservedPlate]:
    """Lectura de cada plato por id canónico. Agrega a `aliases` los nuevos."""
    known = set(prev.plates) | set(aliases)
    new_items = [i for i in items if i.track_id not in known]
    new_per_position = Counter(i.position for i in new_items)
    prev_per_position = Counter(p.position for p in prev.plates.values())

    readings: dict[str, ObservedPlate] = {}
    fresh: set[str] = set()  # canónicos que ya tienen la lectura de un id nuevo
    for item in items:
        if item.track_id in aliases:
            canonical, is_alias = aliases[item.track_id], True
        elif item.track_id in prev.plates:
            canonical, is_alias = item.track_id, False
        elif (
            new_per_position[item.position] == 1
            and prev_per_position[item.position] == 1
        ):
            canonical = next(
                p.track_id for p in prev.plates.values() if p.position == item.position
            )
            aliases[item.track_id], is_alias = canonical, True
        else:
            canonical, is_alias = item.track_id, False  # plato nuevo

        # Si el viejo (retenido por el suavizado) y el nuevo caen en el mismo plato,
        # gana el nuevo: es la lectura fresca.
        if canonical in fresh:
            continue
        readings[canonical] = item
        if is_alias:
            fresh.add(canonical)
    return readings


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
