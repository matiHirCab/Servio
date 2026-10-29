"""El pipeline de una lectura (A-2): un frame → recorte por mesa → classify() →
suavizado → observación → `on_observation`.

Sale de `main.py` para que el loop sea una pieza que `app.py` arma y `vista_demo`
reutiliza, sin flags ni variables de entorno adentro. Ver docs/context/04-vision.md.

- Las mesas se clasifican en paralelo (una llamada por mesa), así una lectura no
  tarda la suma de todas.
- Una mesa cuya lectura falla (`None` o error) no manda observación esa vez.
"""

import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import numpy as np

from servio_vision.alerts.models import TableObservation
from servio_vision.classifier import Classifier
from servio_vision.dish_match import DishMatcher
from servio_vision.restaurant import Restaurant, plate_order
from servio_vision.schemas import Item, TableReport
from servio_vision.smoothing import TableSmoother
from servio_vision.tables import TableTracker
from servio_vision.zones import Zones, crop_table, remap_bbox, table_rect, to_jpeg

logger = logging.getLogger("servio")

MAX_TABLE_WORKERS = 8  # mesas que se clasifican a la vez

ObservationHandler = Callable[[TableObservation], object]


class Pipeline:
    """Una lectura = un frame: clasifica y suaviza cada mesa y arma su observación."""

    def __init__(
        self,
        classifier: Classifier,
        zones: Zones | None,
        on_observation: ObservationHandler,
        finished_after: float | None = None,
        tables: TableTracker | None = None,
        restaurant: Restaurant | None = None,
        dish_matcher: DishMatcher | None = None,
        smoother_factory: Callable[[], TableSmoother] | None = None,
    ) -> None:
        """`zones` fijas (de un archivo) o `tables`, que detecta las mesas en cada
        lectura. `smoother_factory`: cómo armar el suavizado de cada mesa (app.py le
        pasa los umbrales de la configuración); sin él, `TableSmoother` por defecto."""
        if (zones is None) == (tables is None):
            raise ValueError("Pipeline necesita zones o tables, no las dos")
        self.classifier = classifier
        self.zones = zones or Zones(frame_size=(1, 1), tables=[])
        self.tables = tables
        self.restaurant = restaurant
        self.dish_matcher = dish_matcher
        self.on_observation = on_observation
        self.finished_after = finished_after
        self._new_smoother = smoother_factory or (
            lambda: TableSmoother(self.finished_after)
        )
        self.smoothers: dict[str, TableSmoother] = {}
        self._rects: dict[str, tuple[int, int, int, int]] = {}  # recorte de cada mesa
        self.previous: dict[str, list[Item]] = {}
        # De la última lectura, para el latido (health.py): cuántas observaciones
        # aceptaron los avisos y si falló la detección de mesas.
        self.delivered = 0
        self.detection_failed = False
        self._add_tables(self.zones)
        self._pool = ThreadPoolExecutor(max_workers=MAX_TABLE_WORKERS)

    @property
    def frame_ok(self) -> bool:
        """¿La última lectura llegó a los avisos? Si todas las mesas fallaron (el
        modelo caído) o `alerts` falló en cada una, el loop sigue andando pero los
        mozos no se enteran de nada. Sin mesas detectadas (y sin que la detección
        haya fallado) no hay nada que avisar: una sala vacía está bien."""
        return self.delivered > 0 or (
            not self.zones.tables and not self.detection_failed
        )

    def _follow_moved_tables(self, frame: np.ndarray) -> None:
        """Si el recorte de una mesa cambió, las cajas de sus platos (relativas al
        recorte) se pasan al recorte nuevo: así el clasificador y el suavizado siguen
        reconociendo los mismos platos aunque la mesa se haya movido."""
        for zone in self.zones.tables:
            try:
                rect = table_rect(frame, zone, self.zones)
            except ValueError:
                continue
            old = self._rects.get(zone.table_id)
            self._rects[zone.table_id] = rect
            if old is None or old == rect:
                continue

            def remap(bbox, old=old, new=rect):
                return remap_bbox(bbox, old, new)

            self.previous[zone.table_id] = [
                i.model_copy(update={"bbox": remap(i.bbox)}) if i.bbox else i
                for i in self.previous[zone.table_id]
            ]
            self.smoothers[zone.table_id].remap_boxes(remap)
            ids = getattr(self.classifier, "ids", None)  # memoria de ids (YOLO)
            if ids is not None:
                ids.remap(zone.table_id, remap)

    def _identify_dishes(
        self, frame: np.ndarray, table_id: str, items: list[Item]
    ) -> None:
        """Platos nuevos de la mesa: cuál del pedido es cada uno (una vez por plato)."""
        if self.restaurant is None or self.dish_matcher is None:
            return
        zone = next((z for z in self.zones.tables if z.table_id == table_id), None)
        if zone is None:
            return
        for item in sorted(items, key=lambda i: plate_order(f"{table_id}-{i.id}")):
            track_id = f"{table_id}-{item.id}"
            if item.bbox is None or self.restaurant.is_assigned(table_id, track_id):
                continue
            options = self.restaurant.unassigned(table_id)
            if not options:
                return
            x0, y0, x1, y1 = table_rect(frame, zone, self.zones)
            bx, by, bw, bh = item.bbox
            w, h = x1 - x0, y1 - y0
            crop = frame[
                int(y0 + by * h) : int(y0 + (by + bh) * h),
                int(x0 + bx * w) : int(x0 + (bx + bw) * w),
            ]
            if crop.size == 0:
                continue
            dish = self.dish_matcher.match(to_jpeg(crop), options)
            if dish is not None:
                self.restaurant.assign(table_id, track_id, dish)

    def _only_ordered(self, table_id: str, items: list[Item]) -> list[Item]:
        """Con la comanda, un "plato" que no es ninguno de los pedidos de la mesa no
        es un plato (una caja, una taza…): no cuenta para los avisos ni se dibuja."""
        if self.restaurant is None or not self.restaurant.has_order(table_id):
            return items
        kept = []
        for item in items:
            track_id = f"{table_id}-{item.id}"
            if self.restaurant.is_assigned(table_id, track_id) or self.restaurant.dish(
                table_id, track_id
            ):
                kept.append(item)
        self.previous[table_id] = kept
        return kept

    def _add_tables(self, zones: Zones) -> None:
        for t in zones.tables:
            if t.table_id not in self.smoothers:
                self.smoothers[t.table_id] = self._new_smoother()
            self.previous.setdefault(t.table_id, [])

    def process_frame(self, frame: np.ndarray, ts: datetime) -> list[TableObservation]:
        """Procesa un frame. Un error en una mesa descarta esa mesa, no corta el loop."""
        self.delivered = 0
        self.detection_failed = False
        if self.tables is not None:
            try:
                self.zones = self.tables.update(frame)
                self._add_tables(self.zones)
                self._follow_moved_tables(frame)
            except Exception:
                self.detection_failed = True
                logger.exception("%s: no se pudieron detectar las mesas", ts)
        futures = {}
        for zone in self.zones.tables:
            try:
                jpeg = to_jpeg(crop_table(frame, zone, self.zones))
            except Exception:
                logger.exception("%s %s: no se pudo recortar", ts, zone.table_id)
                continue
            futures[zone.table_id] = self._pool.submit(
                self._classify, jpeg, zone.table_id
            )
        observations = []
        for table_id, future in futures.items():
            report = future.result()
            if report is None:
                logger.warning("%s %s: lectura fallida, se descarta", ts, table_id)
                continue
            try:
                items = self.smoothers[table_id].update(report.items, ts)
                self.previous[table_id] = items
                if self.restaurant is not None:
                    self.restaurant.release_missing(
                        table_id, {f"{table_id}-{i.id}" for i in items}
                    )
                self._identify_dishes(frame, table_id, items)
                items = self._only_ordered(table_id, items)
                observation = self._observation(table_id, ts, report, items)
            except Exception:
                logger.exception("%s %s: no se pudo armar la observación", ts, table_id)
                continue
            try:
                self.on_observation(observation)
                self.delivered += 1
            except Exception:
                logger.exception(
                    "%s %s: alerts falló con esta observación", ts, table_id
                )
            observations.append(observation)
        return observations

    def close(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)

    def _classify(self, jpeg: bytes, table_id: str) -> TableReport | None:
        try:
            return self.classifier.classify(jpeg, table_id, self.previous[table_id])
        except Exception:
            logger.exception("%s: el clasificador falló", table_id)
            return None

    def _observation(
        self, table_id: str, ts: datetime, report: TableReport, items: list[Item]
    ) -> TableObservation:
        return TableObservation(
            schema_version=1,
            camera_id=self.zones.camera_id,
            ts=ts,
            table_id=table_id,
            people_present=report.people_present,
            items=[
                {
                    "track_id": f"{table_id}-{item.id}",
                    "state": item.state,
                    "fill_ratio": item.fill_ratio,
                    "confidence": item.confidence,
                    "position": item.position,
                }
                for item in items
            ],
        )
