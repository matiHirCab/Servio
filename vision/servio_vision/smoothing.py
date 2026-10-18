"""Suavizado de estados por plato (JES-5).

Reglas de docs/context/04-vision.md («Suavizado»):
- Un plato con `visible: false` no cuenta como lectura (ni para cambiar de
  estado ni como ausencia).
- Cambio a un estado vecino hacia adelante (`lleno → comiendo`): se acepta con
  2 lecturas seguidas.
- Retroceso (`vacio → comiendo`) o salto de más de un estado (`lleno → vacio`):
  se acepta con 3 lecturas seguidas con `confidence ≥ 0.8`.
- Ausente en 1 lectura: se mantiene. Ausente en 2 seguidas: retirado.

Decisiones donde el doc no dice nada:
- Un plato nuevo entra con su estado desde la primera lectura.
- Un plato tapado o ausente una sola vez sigue saliendo con su último estado
  aceptado, para que `alerts` no lo vea desaparecer y cierre avisos de más.
- `state`, `fill_ratio` y `confidence` salen de la última lectura que
  coincidió con el estado aceptado, así nunca se contradicen. `position` y
  `bbox` salen siempre de la lectura visible más reciente, aunque el cambio de
  estado esté pendiente, para que el plato se dibuje y empareje donde está.

Plato terminado por tiempo (`finished_after`, en segundos de video): un plato
que queda en `por_terminar` ese tiempo pasa a `vacio` (quedan restos que el
cliente no va a comer). Después, las lecturas `por_terminar` no lo devuelven
atrás; solo un plato que se vuelve a servir (`lleno`/`comiendo`) sale de `vacio`.
Necesita el `ts` de cada lectura (`update(items, ts)`).

Uso: un `TableSmoother` por mesa; `update()` con los ítems de cada lectura
válida (si el clasificador devolvió `None`, no se llama).
"""

from dataclasses import dataclass
from datetime import datetime

from servio_vision.schemas import Item, PlateState

STATE_ORDER: tuple[PlateState, ...] = ("lleno", "comiendo", "por_terminar", "vacio")
NEIGHBOR_READINGS = 2
HARD_READINGS = 3
HARD_MIN_CONFIDENCE = 0.8
MISSING_TO_REMOVE = 2


def is_hard_change(current: PlateState, new: PlateState) -> bool:
    """True si es un retroceso o un salto de más de un estado."""
    step = STATE_ORDER.index(new) - STATE_ORDER.index(current)
    return step != 1


@dataclass
class _Track:
    accepted: Item  # última lectura que coincidió con el estado aceptado
    candidate: PlateState | None = None
    streak: int = 0  # lecturas seguidas (válidas) del candidato
    missing: int = 0  # lecturas seguidas en las que no vino
    state_since: datetime | None = None  # desde cuándo tiene el estado aceptado
    finished: bool = False  # pasó a vacio por tiempo en por_terminar


class TableSmoother:
    """Suaviza los platos de una mesa entre lecturas."""

    def __init__(self, finished_after: float | None = None) -> None:
        self._tracks: dict[str, _Track] = {}
        self.finished_after = finished_after

    def update(self, items: list[Item], ts: datetime | None = None) -> list[Item]:
        """Procesa una lectura y devuelve los platos suavizados de la mesa."""
        present = {item.id for item in items}
        for item in items:
            if not item.visible:
                continue  # tapado: no cuenta como lectura
            track = self._tracks.get(item.id)
            if track is None:
                self._tracks[item.id] = _Track(accepted=item, state_since=ts)
            else:
                track.missing = 0
                before = track.accepted.state
                self._observe(track, item)
                if track.accepted.state != before:
                    track.state_since = ts
                    track.finished = False
            self._finish_by_time(self._tracks[item.id], ts)

        for item_id in list(self._tracks):
            if item_id in present:
                continue
            track = self._tracks[item_id]
            track.missing += 1
            if track.missing >= MISSING_TO_REMOVE:
                del self._tracks[item_id]

        return [track.accepted for track in self._tracks.values()]

    def remap_boxes(self, remap) -> None:
        """Pasa las cajas de los platos al recorte nuevo de la mesa (si se movió)."""
        for track in self._tracks.values():
            if track.accepted.bbox is not None:
                track.accepted = track.accepted.model_copy(
                    update={"bbox": remap(track.accepted.bbox)}
                )

    def _finish_by_time(self, track: _Track, ts: datetime | None) -> None:
        """`por_terminar` durante `finished_after` segundos → `vacio`."""
        if (
            self.finished_after is None
            or ts is None
            or track.state_since is None
            or track.accepted.state != "por_terminar"
        ):
            return
        if (ts - track.state_since).total_seconds() >= self.finished_after:
            track.accepted = track.accepted.model_copy(
                update={"state": "vacio", "fill_ratio": 0.0}
            )
            track.state_since, track.finished = ts, True
            track.candidate, track.streak = None, 0

    def _observe(self, track: _Track, item: Item) -> None:
        current = track.accepted.state
        new = item.state
        if track.finished and new in ("por_terminar", "vacio"):
            # terminado por tiempo: los restos no lo devuelven a por_terminar
            track.accepted = _keep_box(
                track.accepted.model_copy(
                    update={"position": item.position, "bbox": item.bbox}
                ),
                track.accepted,
            )
            track.candidate, track.streak = None, 0
            return
        if new == current:
            track.accepted = _keep_box(item, track.accepted)
            track.candidate, track.streak = None, 0
            return

        # el estado espera confirmación, pero el plato está donde se lo ve ahora
        track.accepted = _keep_box(
            track.accepted.model_copy(
                update={"position": item.position, "bbox": item.bbox}
            ),
            track.accepted,
        )
        if new != track.candidate:
            track.candidate, track.streak = new, 0
        hard = is_hard_change(current, new)
        if hard and (item.confidence or 0) < HARD_MIN_CONFIDENCE:
            # para un cambio difícil, una lectura dudosa corta la racha
            track.streak = 0
            return
        track.streak += 1
        if track.streak >= (HARD_READINGS if hard else NEIGHBOR_READINGS):
            track.accepted = _keep_box(item, track.accepted)
            track.candidate, track.streak = None, 0


def _keep_box(item: Item, previous: Item) -> Item:
    """Si la lectura nueva no trae caja, conserva la que se conocía del plato."""
    if item.bbox is None and previous.bbox is not None:
        return item.model_copy(update={"bbox": previous.bbox})
    return item
