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

Uso: un `TableSmoother` por mesa; `update()` con los ítems de cada lectura
válida (si el clasificador devolvió `None`, no se llama).
"""

from dataclasses import dataclass

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


class TableSmoother:
    """Suaviza los platos de una mesa entre lecturas."""

    def __init__(self) -> None:
        self._tracks: dict[str, _Track] = {}

    def update(self, items: list[Item]) -> list[Item]:
        """Procesa una lectura y devuelve los platos suavizados de la mesa."""
        present = {item.id for item in items}
        for item in items:
            if not item.visible:
                continue  # tapado: no cuenta como lectura
            track = self._tracks.get(item.id)
            if track is None:
                self._tracks[item.id] = _Track(accepted=item)
            else:
                track.missing = 0
                self._observe(track, item)

        for item_id in list(self._tracks):
            if item_id in present:
                continue
            track = self._tracks[item_id]
            track.missing += 1
            if track.missing >= MISSING_TO_REMOVE:
                del self._tracks[item_id]

        return [track.accepted for track in self._tracks.values()]

    def _observe(self, track: _Track, item: Item) -> None:
        current = track.accepted.state
        new = item.state
        if new == current:
            track.accepted = item
            track.candidate, track.streak = None, 0
            return

        # el estado espera confirmación, pero el plato está donde se lo ve ahora
        track.accepted = track.accepted.model_copy(
            update={"position": item.position, "bbox": item.bbox}
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
            track.accepted = item
            track.candidate, track.streak = None, 0
