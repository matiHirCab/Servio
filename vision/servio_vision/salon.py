"""Estado del salón en vivo (F-1, vista de reconocimiento).

Después de cada lectura, `SalonFeed.on_reading` (lo llama el pipeline) arma una
foto del salón como JSON: dónde está cada mesa en la imagen de la cámara, sus
platos (estado, cuánto queda, caja y nombre del plato del pedido), quién la
atiende, si hay gente y los avisos abiertos. `salon_web.py` la sirve en
`/<local>/salon` y la empuja al navegador apenas cambia.

La foto se arma en el hilo del pipeline y queda inmutable: el servidor web solo
la lee, así no comparte nada con las reglas de avisos.

Modo video (F-1b, decisión 2): con `video=True` el feed guarda además el último
frame **en memoria** como JPEG (`keep_frame`, lo llama el loop de `app.run` con el
frame ya difuminado; uno solo, se pisa en cada lectura) y `salon_web.py` lo sirve
en `/<local>/salon/frame.jpg`. Apagado, no se guarda ni se sirve nada.

Coordenadas: todo normalizado 0–1 en la imagen de la cámara ([x, y, w, h]).
"""

import threading
from collections import Counter
from collections.abc import Iterable
from datetime import datetime
from typing import TYPE_CHECKING, Any

import numpy as np

from servio_vision.alerts.models import TableObservation
from servio_vision.alerts.state import table_status
from servio_vision.restaurant import Restaurant, plate_order
from servio_vision.schemas import PlateState
from servio_vision.zones import to_jpeg

if TYPE_CHECKING:
    from servio_vision.alerts.processor import AlertProcessor
    from servio_vision.pipeline import Pipeline

PLATE_STATES = ("lleno", "comiendo", "por_terminar", "vacio")
Box = tuple[float, float, float, float]


def _iso(ts: datetime | None) -> str | None:
    return ts.isoformat() if ts is not None else None


def table_box(polygon: Iterable[tuple[int, int]], frame_size: tuple[int, int]) -> Box:
    """Rectángulo que encierra el polígono de la mesa, normalizado en la imagen."""
    xs, ys = zip(*polygon)
    width, height = frame_size
    x0, y0 = max(min(xs) / width, 0.0), max(min(ys) / height, 0.0)
    x1, y1 = min(max(xs) / width, 1.0), min(max(ys) / height, 1.0)
    return (x0, y0, max(x1 - x0, 0.0), max(y1 - y0, 0.0))


def plate_box(bbox: Box | None, table: Box) -> Box | None:
    """Caja de un plato (normalizada en el recorte de su mesa) → en la imagen."""
    if bbox is None:
        return None
    tx, ty, tw, th = table
    bx, by, bw, bh = bbox
    return (tx + bx * tw, ty + by * th, bw * tw, bh * th)


def build_snapshot(
    pipeline: "Pipeline",
    last: dict[str, TableObservation],
    ts: datetime,
    local_id: str,
    processor: "AlertProcessor | None" = None,
    restaurant: Restaurant | None = None,
    interval: float | None = None,
    video: bool = False,
) -> dict[str, Any]:
    """La foto del salón después de una lectura. `last`: la última observación de
    cada mesa (una mesa que esta vez no se pudo leer muestra la anterior). `video`:
    si la página puede pedir `frame.jpg` (sin eso no muestra el modo video)."""
    zones = pipeline.zones
    tables: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    for zone in zones.tables:
        table_id = zone.table_id
        box = table_box(zone.polygon, zones.frame_size)
        obs = last.get(table_id)
        boxes = {
            f"{table_id}-{item.id}": item.bbox
            for item in pipeline.previous.get(table_id, [])
        }
        plates: list[dict[str, Any]] = []
        states: list[PlateState] = []
        for plate in sorted(
            obs.items if obs else [], key=lambda p: plate_order(p.track_id)
        ):
            dish = restaurant.dish_of(table_id, plate.track_id) if restaurant else None
            states.append(plate.state)
            plates.append(
                {
                    "id": plate.track_id,
                    "state": plate.state,
                    "fill_ratio": plate.fill_ratio,
                    "bbox": plate_box(boxes.get(plate.track_id), box),
                    "dish": dish.name if dish else None,
                }
            )
        state = processor.tables.get(table_id) if processor is not None else None
        active = state.active_plates() if state is not None else []
        counts.update(states)
        tables.append(
            {
                "id": table_id,
                "name": zone.name,
                "box": box,
                "status": table_status(states),
                "people": obs.people_present if obs else False,
                "waiter": restaurant.waiter(table_id) if restaurant else None,
                "since": _iso(min((p.first_seen for p in active), default=None)),
                "read_at": _iso(obs.ts) if obs else None,
                "plates": plates,
            }
        )
    alerts = []
    if processor is not None:
        for alert in sorted(processor.open_alerts.values(), key=lambda a: a.created_at):
            if alert.status != "abierta":
                continue
            alerts.append(
                {
                    "id": alert.id,
                    "table_id": alert.table_id,
                    "type": alert.type,
                    "message": alert.message,
                    "created_at": _iso(alert.created_at),
                }
            )
    return {
        "local": local_id,
        "camera": zones.camera_id,
        "ts": _iso(ts),
        "interval": interval,
        "video": video,
        "frame": list(zones.frame_size),
        "tables": tables,
        "alerts": alerts,
        "counts": {s: counts.get(s, 0) for s in PLATE_STATES},
    }


class SalonFeed:
    """La última foto del salón y una forma de esperar la siguiente (para el
    navegador). `on_reading` va en `Pipeline(on_reading=...)`."""

    def __init__(
        self,
        local_id: str,
        processor: "AlertProcessor | None" = None,
        restaurant: Restaurant | None = None,
        interval: float | None = None,
        video: bool = False,
    ) -> None:
        self.local_id = local_id
        self.processor = processor
        self.restaurant = restaurant
        self.interval = interval
        self.video = video
        self._last: dict[str, TableObservation] = {}
        self._snapshot: dict[str, Any] | None = None
        self._version = 0
        self._changed = threading.Condition()
        self._frame: bytes | None = None

    def keep_frame(self, frame: np.ndarray) -> None:
        """Guarda el frame (BGR, ya difuminado) como el JPEG que sirve `frame.jpg`.
        Solo con `video`; si no, no se guarda nada (ni se codifica)."""
        if not self.video:
            return
        jpeg = to_jpeg(frame)
        with self._changed:
            self._frame = jpeg

    def frame_jpeg(self) -> bytes | None:
        """El último frame guardado, o `None` (modo video apagado o sin lecturas)."""
        with self._changed:
            return self._frame

    def on_reading(
        self, pipeline: "Pipeline", observations: list[TableObservation], ts: datetime
    ) -> None:
        for obs in observations:
            self._last[obs.table_id] = obs
        present = {z.table_id for z in pipeline.zones.tables}
        self._last = {k: v for k, v in self._last.items() if k in present}
        snapshot = build_snapshot(
            pipeline,
            self._last,
            ts,
            self.local_id,
            self.processor,
            self.restaurant,
            self.interval,
            self.video,
        )
        with self._changed:
            self._version += 1
            snapshot["version"] = self._version
            self._snapshot = snapshot
            self._changed.notify_all()

    def snapshot(self) -> dict[str, Any] | None:
        with self._changed:
            return self._snapshot

    def wait(self, after: int, timeout: float) -> tuple[int, dict[str, Any] | None]:
        """Espera una foto más nueva que la versión `after` (hasta `timeout` s) y
        devuelve (versión, foto); si no llegó, la misma versión."""
        with self._changed:
            self._changed.wait_for(lambda: self._version > after, timeout)
            return self._version, self._snapshot
