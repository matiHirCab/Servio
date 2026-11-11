"""Clasificador `yolo` (TOM-4): solo YOLO, plan B sin internet.

Encuentra los platos y les da estado según la clase del modelo entrenado:
`food` → `lleno`, `finished_food` → `vacio`. Con YOLOE sin entrenar (sin
`YOLO_WEIGHTS`) encuentra los platos pero no sabe su estado, y devuelve `None`.
"""

import io
import logging
from dataclasses import dataclass

from PIL import Image

from servio_vision.detector import Detection, PlateDetector
from servio_vision.schemas import Item, PlateState, TableReport
from servio_vision.tracking import IdCounter, match_ids, position_label

logger = logging.getLogger(__name__)

STATE_FROM_LABEL: dict[str, PlateState] = {"food": "lleno", "finished_food": "vacio"}


@dataclass
class FoundPlate:
    id: str
    bbox: tuple[float, float, float, float]  # normalizada 0–1
    detection: Detection | None  # None: plato que YOLO perdió (lo agrega el hybrid)


def decode(image: bytes) -> Image.Image:
    return Image.open(io.BytesIO(image)).convert("RGB")


def find_plates(
    detector: PlateDetector,
    img: Image.Image,
    previous_items: list[Item],
    ids: IdCounter | None = None,
    table_id: str = "",
) -> list[FoundPlate]:
    """Detecta los platos y les asigna ids estables, ordenados de arriba a abajo."""
    dets = detector.detect(img)
    w, h = img.size
    bboxes = [
        (
            d.box[0] / w,
            d.box[1] / h,
            (d.box[2] - d.box[0]) / w,
            (d.box[3] - d.box[1]) / h,
        )
        for d in dets
    ]
    if ids:
        plate_ids = ids.assign(table_id, bboxes, previous_items)
    else:
        plate_ids = match_ids(bboxes, previous_items)
    plates = [FoundPlate(i, b, d) for i, b, d in zip(plate_ids, bboxes, dets)]
    plates.sort(key=lambda p: (round(p.bbox[1] * 4), p.bbox[0]))
    return plates


def items_from_labels(plates: list[FoundPlate], confidence_factor: float = 1.0):
    """Items con el estado de la clase de YOLO. Saltea las clases sin estado y
    devuelve `None` si no queda ningún plato con estado (p. ej., YOLOE sin entrenar)."""
    items = []
    for p in plates:
        if p.detection is None:  # un plato perdido: no lo vio YOLO, no hay clase
            continue
        state = STATE_FROM_LABEL.get(p.detection.label)
        if state is None:
            logger.debug("clase %r sin estado, salteo %s", p.detection.label, p.id)
            continue
        items.append(
            Item(
                id=p.id,
                state=state,
                position=position_label(p.bbox),
                bbox=p.bbox,
                confidence=round(p.detection.confidence * confidence_factor, 3),
            )
        )
    if plates and not items:
        return None
    return items


class YoloClassifier:
    def __init__(self, detector: PlateDetector | None = None, **detector_kwargs):
        self.detector = detector or PlateDetector(**detector_kwargs)
        self.ids = IdCounter()

    def classify(
        self, image: bytes, table_id: str, previous_items: list[Item]
    ) -> TableReport | None:
        plates = find_plates(
            self.detector, decode(image), previous_items, self.ids, table_id
        )
        items = items_from_labels(plates)
        if items is None:
            logger.warning(
                "Mesa %s: el detector no da estado (¿falta YOLO_WEIGHTS entrenado?)",
                table_id,
            )
            return None
        return TableReport(items=items)
