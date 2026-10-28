"""Detector de platos con YOLO (TOM-4). Lo usan los clasificadores `yolo` e `hybrid`.

- Sin `YOLO_WEIGHTS`: YOLOE preentrenado con los prompts "plate" y "bowl" (sale de
  test-yolo-world: 11/11 platos en el video de prueba con `imgsz=1280`). Encuentra
  platos pero no sabe si están llenos.
- Con `YOLO_WEIGHTS`: el `yolov8n` entrenado en Colab con `food` / `finished_food`.

`ultralytics` está en `requirements-yolo.txt` y se importa recién al crear el detector.
"""

import logging
import os
import statistics
from dataclasses import dataclass

from servio_vision import settings

logger = logging.getLogger(__name__)

DEFAULT_YOLOE = "yoloe-11s-seg.pt"
PLATE_PROMPTS = ("plate", "bowl")


@dataclass
class Detection:
    box: tuple[float, float, float, float]  # x1, y1, x2, y2 en píxeles
    confidence: float
    label: str  # "plate", "bowl", "food", "finished_food"…


def _iou(a, b) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / (union + 1e-9)


def _contained(a, b, threshold: float = 0.8) -> bool:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    smaller = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
    return ix * iy >= threshold * smaller


def filter_detections(dets: list[Detection], min_rel: float = 0.35) -> list[Detection]:
    """Saca duplicados (el mismo plato como plate y bowl) y cajas mucho más chicas
    que el plato típico (mitades de palta, aceitunas…)."""
    kept: list[Detection] = []
    for d in sorted(dets, key=lambda d: -d.confidence):
        if all(_iou(d.box, k.box) < 0.4 and not _contained(d.box, k.box) for k in kept):
            kept.append(d)
    if not kept:
        return []
    areas = [(d.box[2] - d.box[0]) * (d.box[3] - d.box[1]) for d in kept]
    typical = statistics.median(areas)
    return [d for d, a in zip(kept, areas) if a >= min_rel * typical]


class PlateDetector:
    def __init__(
        self,
        weights: str | None = None,
        conf: float = 0.25,
        imgsz: int = 1280,
        min_rel: float = 0.35,
        device: str | None = None,
    ):
        if weights is None or device is None:
            # lo que no vino por parámetro (app.py lo pasa) sale de la configuración
            cfg = settings.current()
            weights = weights or (str(cfg.yolo_weights) if cfg.yolo_weights else None)
            device = device or cfg.yolo_device
        if weights and not os.path.exists(weights):
            logger.warning("YOLO_WEIGHTS=%s no existe, uso YOLOE", weights)
            weights = None
        if weights:
            from ultralytics import YOLO

            self.model = YOLO(weights)
        else:
            from ultralytics import YOLOE  # recién en ultralytics 8.3.99+

            self.model = YOLOE(DEFAULT_YOLOE)
            self.model.set_classes(
                list(PLATE_PROMPTS), self.model.get_text_pe(list(PLATE_PROMPTS))
            )
        self.conf = conf
        self.imgsz = imgsz
        self.min_rel = min_rel
        # torchvision de Windows suele ser build de CPU: por defecto, CPU
        self.device = device or "cpu"

    def detect(self, image) -> list[Detection]:
        """`image`: PIL.Image o array RGB."""
        r = self.model.predict(
            image,
            conf=self.conf,
            imgsz=self.imgsz,
            device=self.device,
            verbose=False,
        )[0]
        names = r.names
        dets = [
            Detection(tuple(box), float(c), names[int(k)])
            for box, c, k in zip(
                r.boxes.xyxy.tolist(), r.boxes.conf.tolist(), r.boxes.cls.tolist()
            )
        ]
        return filter_detections(dets, self.min_rel)
