"""Estado del salón en vivo (F-1, vista de reconocimiento).

Después de cada lectura, `SalonFeed.on_reading` (lo llama el pipeline) arma una
foto del salón como JSON: dónde está cada mesa en la imagen de la cámara, sus
platos (estado, cuánto queda, caja y nombre del plato del pedido), quién la
atiende, si hay gente y los avisos abiertos. `salon_web.py` la sirve en
`/<local>/salon` y la empuja al navegador apenas cambia.

La foto se arma en el hilo del pipeline y queda inmutable: el servidor web solo
la lee, así no comparte nada con las reglas de avisos.

Modo video (F-1b, decisión 2): con `video=True` el feed guarda además, **en
memoria**, el frame (ya difuminado) que `on_reading` recibe con cada lectura, junto
con la foto: el frame y las capas que se dibujan encima son siempre del mismo
cuadro. Se codifica a JPEG recién cuando alguien pide `/<local>/salon/frame.jpg`
(`frame_jpeg`), una vez por lectura. Apagado, no se guarda ni se sirve nada.

Coordenadas: todo normalizado 0–1 en la imagen de la cámara ([x, y, w, h]).
"""

import logging
import threading
from collections import Counter
from collections.abc import Iterable
from datetime import datetime
from typing import TYPE_CHECKING, Any

import cv2
import numpy as np

from servio_vision.alerts.models import TableObservation
from servio_vision.alerts.state import table_status
from servio_vision.restaurant import Restaurant, plate_order
from servio_vision.schemas import PlateState
from servio_vision.zones import to_jpeg

if TYPE_CHECKING:
    from servio_vision.alerts.processor import AlertProcessor
    from servio_vision.pipeline import Pipeline

logger = logging.getLogger("servio")

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
    frame_size: tuple[int, int] | None = None,
) -> dict[str, Any]:
    """La foto del salón después de una lectura. `last`: la última observación de
    cada mesa (una mesa que esta vez no se pudo leer muestra la anterior). `video`:
    si la página puede pedir `frame.jpg` (sin eso no muestra el modo video).
    `frame_size`: (ancho, alto) real del frame; sin él, el de las zonas."""
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
        # un frame vacío (captura fallida) no sirve de proporción: van las zonas
        "frame": list(
            frame_size if frame_size and min(frame_size) > 0 else zones.frame_size
        ),
        "tables": tables,
        "alerts": alerts,
        "counts": {s: counts.get(s, 0) for s in PLATE_STATES},
    }


# El frame del modo video no hace falta a resolución de cámara: la página lo muestra
# en una caja de a lo sumo el 72 % del alto de la pantalla, y cada navegador lo
# baja entero en cada lectura por el wifi del local.
DISPLAY_MAX_SIDE = 1280


def display_copy(frame: np.ndarray) -> np.ndarray:
    """Copia del frame (el loop puede reusar el buffer de la cámara), achicada a
    `DISPLAY_MAX_SIDE` de lado mayor si es más grande; las cajas van normalizadas,
    así que a la página le da igual."""
    h, w = frame.shape[:2]
    side = max(h, w)
    if side <= DISPLAY_MAX_SIDE or min(h, w) == 0:
        return frame.copy()
    k = DISPLAY_MAX_SIDE / side
    return cv2.resize(
        frame,
        (max(1, round(w * k)), max(1, round(h * k))),
        interpolation=cv2.INTER_AREA,
    )


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
        self._frame: np.ndarray | None = None  # el de la última foto publicada
        self._jpeg: bytes | None = None  # ese frame codificado, si alguien lo pidió
        self._encode_lock = threading.Lock()  # un solo navegador codifica por vez
        self._encode_failed = 0  # la versión cuyo frame no se pudo codificar

    def frame_jpeg(self) -> tuple[bytes, int] | None:
        """El frame de la última foto como JPEG, con la versión de esa foto (la
        página lo muestra recién cuando dibuja esa foto). Se codifica la primera vez
        que se pide, una vez por lectura aunque lo pidan varios navegadores. `None`:
        modo video apagado, sin lecturas, o un frame que no se pudo codificar (queda
        logueado una vez; la vista sigue sin imagen, el pipeline sigue)."""
        with self._changed:
            frame, jpeg, version = self._frame, self._jpeg, self._version
        if frame is None:
            return None
        if jpeg is not None:
            return jpeg, version  # lo ya codificado sale sin esperar a nadie
        # el pipeline nunca espera este candado: `on_reading` solo toma `_changed`
        with self._encode_lock:
            with (
                self._changed
            ):  # lo de ahora: otro pudo codificarlo, o hay lectura nueva
                frame, jpeg, version = self._frame, self._jpeg, self._version
            if frame is None:
                return None
            if jpeg is not None:
                return jpeg, version
            if self._encode_failed == version:
                return None
            try:
                jpeg = to_jpeg(frame)
            except (ValueError, cv2.error) as e:
                logger.warning("No se pudo codificar el frame para la vista: %s", e)
                self._encode_failed = version
                return None
            with self._changed:
                if self._version == version:  # si no, ya hay otra lectura
                    self._jpeg = jpeg
            return jpeg, version

    def on_reading(
        self,
        pipeline: "Pipeline",
        observations: list[TableObservation],
        ts: datetime,
        frame: np.ndarray,
    ) -> None:
        """La foto de esta lectura y, con `video`, su frame (una copia: el loop puede
        reusar el buffer de la cámara). La foto lleva el tamaño real del frame: las
        cajas van normalizadas y la página las dibuja sobre la imagen que recibe,
        aunque la cámara no tenga la proporción con que se dibujaron las zonas."""
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
            (int(frame.shape[1]), int(frame.shape[0])),
        )
        kept = display_copy(frame) if self.video else None  # fuera del candado
        with self._changed:
            self._version += 1
            snapshot["version"] = self._version
            self._snapshot = snapshot
            if kept is not None:
                self._frame, self._jpeg = kept, None
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
