"""Detección automática de mesas, en cada lectura (sin zonas a mano).

- `TableDetector`: YOLO entrenado en COCO (clase "dining table"). YOLOE con el
  prompt "table" resultó inestable; COCO encuentra la mesa del video de demo en
  37/37 lecturas (~0,4 s por cuadro en CPU). Necesita `requirements-yolo.txt`.
- `TableTracker`: le da a cada mesa un id estable ("T1", "Mesa 1") aunque se
  mueva, emparejando con la lectura anterior, y suaviza el recuadro para que no
  salte. Si en una lectura no se ve una mesa, mantiene su última posición. Si
  nunca se vio ninguna mesa, usa el cuadro entero como "Mesa 1".

El resultado es un `Zones` como el de `config/zones.json`, así el resto del
pipeline (recorte por mesa, suavizado, avisos) no cambia.
"""

import functools
import logging
from dataclasses import dataclass
from typing import Protocol

import numpy as np

from servio_vision.zones import TableZone, Zones

logger = logging.getLogger(__name__)

Box = tuple[float, float, float, float]  # x0, y0, x1, y1 en píxeles del frame

DEFAULT_TABLE_WEIGHTS = "yolo11s.pt"
COCO_DINING_TABLE = 60
MIN_CONF = 0.15
MARGIN = 0.1  # agranda el recuadro: los platos pueden asomar del borde de la mesa
SMOOTHING = 0.5  # peso de la lectura nueva en el recuadro (el resto, la anterior)
MOVED_IOU = 0.6  # por debajo, la mesa se movió: se salta a la posición nueva
MATCH_IOU = 0.2  # superposición mínima para que sea la misma mesa
NEW_TABLE_MAX_IOU = 0.05  # una detección que pisa una mesa conocida no es otra mesa
# Una detección que contiene a la mesa conocida y es mucho más grande es la mesa
# con algo pegado (las piernas de alguien, una silla): se ignora.
SWALLOW_GROWTH = 1.5


class BoxDetector(Protocol):
    def detect(self, frame: np.ndarray) -> list[tuple[Box, float]]: ...


@functools.lru_cache(maxsize=4)
def coco_model(weights: str = DEFAULT_TABLE_WEIGHTS):
    """El YOLO de COCO cargado una sola vez por pesos: lo comparten las mesas y las
    personas (`people.py`, C-3) en vez de tener dos copias en memoria."""
    from ultralytics import YOLO

    return YOLO(weights)


class TableDetector:
    """Mesas con un YOLO de COCO: [(recuadro, confianza)], la más segura primero."""

    def __init__(self, weights: str = DEFAULT_TABLE_WEIGHTS, conf: float = MIN_CONF):
        self.model = coco_model(weights)
        self.conf = conf

    def detect(self, frame: np.ndarray) -> list[tuple[Box, float]]:
        r = self.model.predict(
            frame,
            conf=self.conf,
            imgsz=640,
            device="cpu",
            classes=[COCO_DINING_TABLE],
            verbose=False,
        )[0]
        found = [
            (tuple(box), float(conf))
            for box, conf in zip(r.boxes.xyxy.tolist(), r.boxes.conf.tolist())
        ]
        return sorted(found, key=lambda d: d[1], reverse=True)


@dataclass
class _Table:
    table_id: str
    box: Box


class TableTracker:
    """Mesas con id estable entre lecturas; `update(frame)` devuelve las zonas."""

    def __init__(self, detector: BoxDetector, camera_id: str = "cam-1") -> None:
        self.detector = detector
        self.camera_id = camera_id
        self.tables: list[_Table] = []
        self._next = 1

    def export_state(self) -> dict:
        """Las mesas con su id y su caja, como JSON: tras un reinicio, la mesa que
        estaba tapada o la que apareció antes no corre los números (T1, T2…)."""
        return {
            "next": self._next,
            "tables": [{"id": t.table_id, "box": list(t.box)} for t in self.tables],
        }

    def restore_state(self, data: dict) -> None:
        self._next = int(data["next"])
        self.tables = [_Table(t["id"], tuple(t["box"])) for t in data["tables"]]

    def update(self, frame: np.ndarray) -> Zones:
        height, width = frame.shape[:2]
        detections = self.detector.detect(frame)
        unmatched = list(detections)
        for table in self.tables:
            best = max(unmatched, key=lambda d: iou(table.box, d[0]), default=None)
            if best is not None and iou(table.box, best[0]) < MATCH_IOU:
                # ¿se movió de golpe? la detección más cercana, si está a menos de
                # un "tamaño de mesa", es la misma mesa
                near = min(unmatched, key=lambda d: _distance(table.box, d[0]))
                best = (
                    near if _distance(table.box, near[0]) < _size(table.box) else None
                )
            if best is not None and _swallows(best[0], table.box):
                unmatched.remove(best)  # es esta mesa, pero no se usa su recuadro
                best = None
            if best is not None:
                moved = iou(table.box, best[0]) < MOVED_IOU
                table.box = best[0] if moved else _blend(table.box, best[0])
                unmatched.remove(best)
            # sin detección: la mesa queda donde estaba
        # mesas nuevas: primero se descartan las repetidas (gana la más segura) y
        # después se numeran por filas, de arriba a abajo y de izquierda a derecha
        new: list[Box] = []
        for box, _conf in unmatched:
            known = [t.box for t in self.tables] + new
            if all(iou(k, box) < NEW_TABLE_MAX_IOU for k in known):
                new.append(box)
        for box in reading_order(new):
            self.tables.append(_Table(f"T{self._next}", box))
            logger.info("Mesa nueva: Mesa %d", self._next)
            self._next += 1
        if not self.tables:
            return _whole_frame(width, height, self.camera_id)
        return Zones(
            camera_id=self.camera_id,
            frame_size=(width, height),
            tables=[
                TableZone(
                    table_id=t.table_id,
                    name=f"Mesa {t.table_id[1:]}",
                    polygon=_polygon(_expand(t.box, width, height)),
                )
                for t in self.tables
            ],
        )


def iou(a: Box, b: Box) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def reading_order(boxes: list[Box]) -> list[Box]:
    """Por filas (de arriba a abajo) y, en cada fila, de izquierda a derecha. Dos
    mesas están en la misma fila si sus centros difieren menos de media mesa en y."""
    rows: list[list[Box]] = []
    for box in sorted(boxes, key=lambda b: _center(b)[1]):
        if rows and abs(_center(box)[1] - _center(rows[-1][0])[1]) < _size(box) / 2:
            rows[-1].append(box)
        else:
            rows.append([box])
    return [b for row in rows for b in sorted(row, key=lambda b: _center(b)[0])]


def _center(box: Box) -> tuple[float, float]:
    return ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)


def _distance(a: Box, b: Box) -> float:
    (ax, ay), (bx, by) = _center(a), _center(b)
    return ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5


def _size(box: Box) -> float:
    return max(box[2] - box[0], box[3] - box[1])


def _area(box: Box) -> float:
    return max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])


def _swallows(new: Box, old: Box) -> bool:
    """`new` tapa casi toda la mesa `old` y es mucho más grande."""
    ix = max(0.0, min(new[2], old[2]) - max(new[0], old[0]))
    iy = max(0.0, min(new[3], old[3]) - max(new[1], old[1]))
    return ix * iy >= 0.9 * _area(old) and _area(new) > SWALLOW_GROWTH * _area(old)


def _blend(old: Box, new: Box) -> Box:
    return tuple(SMOOTHING * n + (1 - SMOOTHING) * o for o, n in zip(old, new))


def _expand(box: Box, width: int, height: int) -> Box:
    x0, y0, x1, y1 = box
    dx, dy = (x1 - x0) * MARGIN, (y1 - y0) * MARGIN
    return (
        max(0.0, x0 - dx),
        max(0.0, y0 - dy),
        min(width - 1.0, x1 + dx),
        min(height - 1.0, y1 + dy),
    )


def _polygon(box: Box) -> list[tuple[int, int]]:
    x0, y0, x1, y1 = (round(v) for v in box)
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def _whole_frame(width: int, height: int, camera_id: str) -> Zones:
    box = (0.0, 0.0, width - 1.0, height - 1.0)
    return Zones(
        camera_id=camera_id,
        frame_size=(width, height),
        tables=[TableZone(table_id="T1", name="Mesa 1", polygon=_polygon(box))],
    )
