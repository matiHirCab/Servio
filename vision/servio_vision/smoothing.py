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

Configurable (`settings.py`: `SMOOTH_READINGS` (2), `SMOOTH_HARD_READINGS` (3) y
`SMOOTH_MIN_CONFIDENCE` (0.8) en el `.env` o el toml; `app.py` los pasa por
parámetro). Menos lecturas o menos confianza = cambia de estado más rápido, pero una
lectura mala pesa más. La Decisions API de OpenAI da confianzas más bajas que Claude.

Uso: un `TableSmoother` por mesa; `update()` con los ítems de cada lectura
válida (si el clasificador devolvió `None`, no se llama).
"""

from dataclasses import dataclass
from datetime import datetime

from servio_vision import settings
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

    def __init__(
        self,
        finished_after: float | None = None,
        *,
        readings: int | None = None,
        hard_readings: int | None = None,
        min_confidence: float | None = None,
    ) -> None:
        """Umbrales por parámetro (los pasa `app.py`); los que faltan salen de la
        configuración del entorno (`settings.current()`, que valida los rangos)."""
        self._tracks: dict[str, _Track] = {}
        self.finished_after = finished_after
        if None in (readings, hard_readings, min_confidence):
            cfg = settings.current()
            readings = cfg.smooth_readings if readings is None else readings
            hard_readings = (
                cfg.smooth_hard_readings if hard_readings is None else hard_readings
            )
            min_confidence = (
                cfg.smooth_min_confidence if min_confidence is None else min_confidence
            )
        self.neighbor_readings = int(readings)
        self.hard_readings = int(hard_readings)
        self.hard_min_confidence = float(min_confidence)

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

    def export_state(self) -> dict:
        """Los platos que sigue, con lo que falta para confirmar un cambio de estado,
        como JSON: tras un reinicio, una sola lectura dudosa no los cambia."""
        return {
            "tracks": {
                item_id: {
                    "accepted": t.accepted.model_dump(mode="json"),
                    "candidate": t.candidate,
                    "streak": t.streak,
                    "missing": t.missing,
                    "state_since": t.state_since.isoformat() if t.state_since else None,
                    "finished": t.finished,
                }
                for item_id, t in self._tracks.items()
            }
        }

    def restore_state(self, data: dict) -> None:
        self._tracks = {
            item_id: _Track(
                accepted=Item.model_validate(t["accepted"]),
                candidate=t["candidate"],
                streak=int(t["streak"]),
                missing=int(t["missing"]),
                state_since=datetime.fromisoformat(t["state_since"])
                if t["state_since"]
                else None,
                finished=bool(t["finished"]),
            )
            for item_id, t in data["tracks"].items()
        }

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
        if hard and (item.confidence or 0) < self.hard_min_confidence:
            # para un cambio difícil, una lectura dudosa corta la racha
            track.streak = 0
            return
        track.streak += 1
        if track.streak >= (self.hard_readings if hard else self.neighbor_readings):
            track.accepted = _keep_box(item, track.accepted)
            track.candidate, track.streak = None, 0


def _keep_box(item: Item, previous: Item) -> Item:
    """Si la lectura nueva no trae caja, conserva la que se conocía del plato."""
    if item.bbox is None and previous.bbox is not None:
        return item.model_copy(update={"bbox": previous.bbox})
    return item
