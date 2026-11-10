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
cuadro. Se codifica a JPEG recién cuando alguien lo pide, una vez
por lectura: la página en modo video lo recibe dentro de la misma foto por SSE
(`/eventos?video=1`) y `frame.jpg` lo sirve suelto para mirar la cámara con un
`curl`. Apagado, no se guarda ni se sirve nada.

Coordenadas: todo normalizado 0–1 en la imagen de la cámara ([x, y, w, h]).
"""

import base64
import json
import logging
import threading
from collections import Counter
from collections.abc import Iterable
from datetime import datetime
from typing import TYPE_CHECKING, Any, NamedTuple

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

BROKEN_FRAME = "El frame de esta lectura no se pudo codificar (ver el log)"

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
    si el servidor ofrece el modo video (la página muestra el segmento y se
    suscribe a la SSE con `?video=1`).
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


def display_size(frame: np.ndarray) -> np.ndarray:
    """El frame achicado a `DISPLAY_MAX_SIDE` de lado mayor si es más grande (las
    cajas van normalizadas, así que a la página le da igual); si no, el mismo.
    Se hace al codificar, no en cada lectura: sin navegadores no cuesta nada."""
    h, w = frame.shape[:2]
    side = max(h, w)
    if side <= DISPLAY_MAX_SIDE or min(h, w) == 0:
        return frame
    k = DISPLAY_MAX_SIDE / side
    return cv2.resize(
        frame,
        (max(1, round(w * k)), max(1, round(h * k))),
        interpolation=cv2.INTER_AREA,
    )


class Published(NamedTuple):
    """Lo que el feed publica con cada lectura, de una vez: la versión, la foto y
    (con `video`) el frame de esa misma lectura. Quien lo toma junto no se
    desfasa aunque llegue otra lectura mientras codifica."""

    version: int
    snapshot: dict[str, Any] | None
    frame: np.ndarray | None


class EncodedFrame(NamedTuple):
    """Lo que `encode` devuelve: el frame de la foto `version` como JPEG, o `None`
    si no se pudo codificar (ya quedó logueado). Distinto de `capture.Frame`."""

    jpeg: bytes | None
    version: int


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
        # lo último que se armó para los navegadores: el frame codificado (o con
        # None si falló) y el JSON de la foto, con y sin frame; una sola caché, de la
        # publicación más nueva que alguien haya pedido
        self._encoded: EncodedFrame | None = None
        self._payloads: dict[bool, tuple[int, bytes]] = {}
        self._encode_lock = threading.Lock()  # un solo navegador codifica por vez
        self._broken_logged = 0  # hasta qué versión ya se avisó un frame roto

    def _cached_frame(self, version: int) -> EncodedFrame | None:
        with self._changed:
            encoded = self._encoded
        return encoded if encoded is not None and encoded.version == version else None

    def encode(self, published: Published) -> EncodedFrame | None:
        """El frame de esa publicación como JPEG. Se codifica la primera vez que se
        pide, una vez por lectura aunque lo pidan varios navegadores; el resultado
        queda para su versión (si es la más nueva pedida). `None`: sin frame (modo
        video apagado o sin lecturas). Un frame que no se pudo codificar vuelve con
        `jpeg=None` (queda logueado una vez; la vista sigue sin imagen, el pipeline
        sigue)."""
        if published.frame is None:
            return None
        hit = self._cached_frame(published.version)
        if hit is not None:
            return hit  # ya resuelto: sale sin esperar a nadie
        # el pipeline nunca espera este candado: `on_reading` solo toma `_changed`
        with self._encode_lock:
            return self._encode_locked(published)

    def _encode_locked(self, published: Published) -> EncodedFrame | None:
        """`encode` con `_encode_lock` ya tomado (también lo usa `payload`)."""
        if published.frame is None:
            return None
        version = published.version
        hit = self._cached_frame(version)  # otro pudo codificarlo mientras esperaba
        if hit is not None:
            return hit
        try:
            result = EncodedFrame(to_jpeg(display_size(published.frame)), version)
        except (ValueError, cv2.error) as e:
            # se avisa una vez por lectura (las versiones solo crecen), aunque una
            # publicación vieja lo vuelva a pedir
            if version > self._broken_logged:
                logger.warning("No se pudo codificar el frame para la vista: %s", e)
                self._broken_logged = version
            result = EncodedFrame(None, version)
        with self._changed:
            if self._encoded is None or self._encoded.version <= version:
                self._encoded = result  # una vieja no pisa una nueva
        return result

    def payload(self, published: Published, with_video: bool) -> bytes:
        """La foto de esa publicación como JSON, armado una vez por lectura para todos
        los suscriptos. Con `with_video`, con el frame de esa misma publicación en
        base64 (`frame_jpeg`), o `frame_error` si no se pudo codificar: imagen y datos
        viajan juntos, nunca se desfasan. Una publicación vieja (un suscripto que
        despertó tarde) se arma igual, sin guardarse: es su foto, y pasa poco."""
        version = published.version
        cached = self._cached_payload(with_video, version)
        if cached is not None:
            return cached
        if not with_video:
            return self._store_payload(
                with_video, version, self._dump(published.snapshot)
            )
        # todos los suscriptos de video se despiertan juntos: el primero codifica y
        # arma, los demás esperan y encuentran el suyo hecho; los sin video no esperan
        with self._encode_lock:
            cached = self._cached_payload(with_video, version)
            if cached is not None:
                return cached
            snapshot = published.snapshot
            found = self._encode_locked(published)
            if found is not None and snapshot is not None:
                snapshot = dict(snapshot)
                if found.jpeg is not None:
                    snapshot["frame_jpeg"] = base64.b64encode(found.jpeg).decode()
                else:
                    snapshot["frame_error"] = BROKEN_FRAME
            return self._store_payload(with_video, version, self._dump(snapshot))

    @staticmethod
    def _dump(snapshot: dict[str, Any] | None) -> bytes:
        return json.dumps(snapshot, ensure_ascii=False).encode()

    def _cached_payload(self, with_video: bool, version: int) -> bytes | None:
        with self._changed:
            cached = self._payloads.get(with_video)
        return cached[1] if cached is not None and cached[0] == version else None

    def _store_payload(self, with_video: bool, version: int, data: bytes) -> bytes:
        with self._changed:
            cached = self._payloads.get(with_video)
            if cached is None or cached[0] <= version:  # una vieja no pisa una nueva
                self._payloads[with_video] = (version, data)
        return data

    def frame_jpeg(self) -> EncodedFrame | None:
        """El frame de la última lectura, codificado (`frame.jpg`); `None` sin frame."""
        return self.encode(self.published())

    def on_reading(
        self,
        pipeline: "Pipeline",
        observations: list[TableObservation],
        ts: datetime,
        frame: np.ndarray,
    ) -> None:
        """La foto de esta lectura y, con `video`, su frame (la misma referencia: el
        loop no lo toca después de procesarlo y `capture` entrega un array nuevo por
        lectura; acá solo se lee para codificarlo). La foto lleva el tamaño real del frame: las
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
        with self._changed:
            self._version += 1
            snapshot["version"] = self._version
            self._snapshot = snapshot
            if self.video:
                self._frame = frame
            self._changed.notify_all()

    def snapshot(self) -> dict[str, Any] | None:
        with self._changed:
            return self._snapshot

    def published(self) -> Published:
        """Lo último publicado: versión, foto y frame, tomados juntos."""
        with self._changed:
            return Published(self._version, self._snapshot, self._frame)

    def wait_published(self, after: int, timeout: float) -> Published:
        """Espera una publicación más nueva que la versión `after` (hasta `timeout`
        s) y la devuelve entera; si no llegó, la actual (misma versión)."""
        with self._changed:
            self._changed.wait_for(lambda: self._version > after, timeout)
            return Published(self._version, self._snapshot, self._frame)
