"""Personas en el recorte de la mesa (C-3, la mitad del modelo).

Al modelo de la nube van **solo recortes de platos** (docs/legal-uruguay.md: un
recorte que muestra solo el plato está disociado y deja de ser un dato personal).
Para eso hacen falta dos cosas en la máquina, que salen de acá:

- **dónde hay personas** en el recorte de la mesa, para pixelar lo que de ellas
  (manos, brazos, torso) entre en el recorte de un plato;
- **si hay gente sentada** (`people_present`), que antes lo decía el modelo mirando
  la mesa entera y ahora no la ve.

El detector es el mismo YOLO de COCO que encuentra las mesas (`tables.py`), con la
clase `person`. Sin ultralytics no hay detector: `hybrid` sigue andando, no manda
la mesa igual, y `people_present` lo estima el modelo con los recortes.
"""

import logging
from typing import Protocol

import numpy as np
from PIL import Image

from servio_vision.privacy import pixelate

logger = logging.getLogger(__name__)

Box = tuple[float, float, float, float]  # x0, y0, x1, y1 en píxeles de la imagen

DEFAULT_WEIGHTS = "yolo11s.pt"  # COCO; los mismos que tables.py
COCO_PERSON = 0
MIN_CONF = 0.25
PIXEL_MARGIN = 0.1  # la caja de la persona se agranda esto de cada lado al pixelar


class PersonDetector(Protocol):
    def detect(self, img: Image.Image) -> list[Box]:
        """Cajas de las personas (o partes: una mano, un brazo) en la imagen."""
        ...


class YoloPersonDetector:
    """`person` de COCO sobre el recorte de la mesa. Desde arriba, una mano o un
    brazo apoyado también salen como `person` (con menos confianza)."""

    def __init__(self, weights: str = DEFAULT_WEIGHTS, conf: float = MIN_CONF) -> None:
        from ultralytics import YOLO

        self.model = YOLO(weights)
        self.conf = conf

    def detect(self, img: Image.Image) -> list[Box]:
        r = self.model.predict(
            img,
            conf=self.conf,
            imgsz=640,
            device="cpu",
            classes=[COCO_PERSON],
            verbose=False,
        )[0]
        return [tuple(box) for box in r.boxes.xyxy.tolist()]


def load_detector() -> PersonDetector | None:
    """El detector, o `None` (con aviso) si no está ultralytics."""
    try:
        return YoloPersonDetector()
    except ImportError:
        logger.warning(
            "Sin ultralytics no se detectan personas: los recortes de plato van sin "
            "pixelar y people_present lo estima el modelo (pip install -r "
            "requirements-yolo.txt)"
        )
        return None


def anonymize_crop(
    crop: Image.Image, origin: tuple[float, float], people: list[Box]
) -> Image.Image:
    """El recorte de un plato con pixelada la parte de cada persona que entra en él.
    `origin`: dónde empieza el recorte en la imagen de la mesa (x, y)."""
    if not people:
        return crop
    ox, oy = origin
    width, height = crop.size
    boxes = []
    for x0, y0, x1, y1 in people:
        # a coordenadas del recorte, con margen, y solo si lo toca
        mx, my = (x1 - x0) * PIXEL_MARGIN, (y1 - y0) * PIXEL_MARGIN
        left, top = max(0.0, x0 - mx - ox), max(0.0, y0 - my - oy)
        right, bottom = (
            min(float(width), x1 + mx - ox),
            min(float(height), y1 + my - oy),
        )
        if right > left and bottom > top:
            boxes.append(
                (int(left), int(top), int(right - left) + 1, int(bottom - top) + 1)
            )
    if not boxes:
        return crop
    pixels = np.asarray(crop).copy()
    pixelate(pixels, boxes, margin=0)
    return Image.fromarray(pixels)
