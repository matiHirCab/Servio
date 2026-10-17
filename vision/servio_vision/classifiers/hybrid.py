"""Clasificador `hybrid` (TOM-5), el de la demo: YOLO encuentra los platos y Claude
estima cuánta comida queda en cada uno.

1. YOLO detecta los platos en el recorte de la mesa y se emparejan los ids.
2. Cada plato se recorta con 10 % de margen.
3. Una llamada a Claude por mesa (`claude_plates.estimate_plates`) con la mesa y los platos.
4. Si Claude falla, usa el estado de la clase de YOLO con confianza más baja; si el
   detector no da estado (YOLOE sin entrenar), la lectura se descarta (`None`).
"""

import io
import logging

from PIL import Image

from servio_vision.classifiers import claude_plates
from servio_vision.classifiers.yolo import decode, find_plates, items_from_labels
from servio_vision.detector import PlateDetector
from servio_vision.schemas import Item, TableReport
from servio_vision.tracking import IdCounter, position_label

logger = logging.getLogger(__name__)

CROP_MARGIN = 0.10
CROP_SIDE = 384  # px del lado mayor de cada recorte de plato
TABLE_SIDE = 1024  # px del lado mayor de la mesa que ve Claude
# Lecturas seguidas sin ver la comida para mostrar un plato como «tapado». Antes de
# eso conserva su última lectura (un brazo que pasa no lo tapa).
HIDDEN_READINGS = 3
FALLBACK_CONFIDENCE = 0.5  # la confianza de YOLO se multiplica por esto si Claude falla


def _jpeg(img: Image.Image, side: int) -> bytes:
    img = img.copy()
    img.thumbnail((side, side))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    return buf.getvalue()


def _crop(img: Image.Image, bbox) -> Image.Image:
    w, h = img.size
    x, y, bw, bh = bbox[0] * w, bbox[1] * h, bbox[2] * w, bbox[3] * h
    mx, my = CROP_MARGIN * bw, CROP_MARGIN * bh
    return img.crop(
        (max(0, x - mx), max(0, y - my), min(w, x + bw + mx), min(h, y + bh + my))
    )


class HybridClassifier:
    def __init__(
        self,
        detector: PlateDetector | None = None,
        provider: str | None = None,
        model: str | None = None,
        timeout_s: float | None = None,
        **detector_kwargs,
    ):
        self.detector = detector or PlateDetector(**detector_kwargs)
        self.ids = IdCounter()
        self.people_present: dict[str, bool] = {}  # último que dijo Claude, por mesa
        self.hidden: dict[tuple[str, str], int] = {}  # lecturas seguidas sin verlo
        self.claude_kwargs = {
            "provider": provider,
            "model": model,
            "timeout_s": timeout_s,
        }

    def classify(
        self, image: bytes, table_id: str, previous_items: list[Item]
    ) -> TableReport | None:
        img = decode(image)
        plates = find_plates(self.detector, img, previous_items, self.ids, table_id)
        if not plates:
            return TableReport(items=[])
        report = claude_plates.estimate_plates(
            _jpeg(img, TABLE_SIDE),
            [_jpeg(_crop(img, p.bbox), CROP_SIDE) for p in plates],
            table_id,
            **self.claude_kwargs,
        )
        if report is None:
            items = items_from_labels(plates, FALLBACK_CONFIDENCE)
            if items is None:
                logger.warning("Mesa %s: Claude falló y YOLO no da estado", table_id)
                return None
            return TableReport(
                items=items,
                people_present=self.people_present.get(table_id, True),
                notes="sin Claude: estado de YOLO",
            )
        self.people_present[table_id] = report.people_present
        last = {i.id: i for i in previous_items if i.state is not None}
        items = []
        for p, est in zip(plates, report.plates):
            key = (table_id, p.id)
            self.hidden[key] = 0 if est.visible else self.hidden.get(key, 0) + 1
            if est.visible:
                reading = {"fill_ratio": est.fill_ratio, "confidence": est.confidence}
            elif p.id in last and self.hidden[key] < HIDDEN_READINGS:
                # tapado hace poco y ya lo vimos: muestra su última lectura, pero
                # con visible=False para que el suavizado no la cuente como nueva
                prev = last[p.id]
                reading = {
                    "fill_ratio": prev.fill_ratio,
                    "state": prev.state,
                    "confidence": prev.confidence,
                    "visible": False,
                }
            else:
                reading = {"visible": False, "confidence": est.confidence}
            items.append(
                Item(
                    id=p.id,
                    position=position_label(p.bbox),
                    bbox=p.bbox,
                    **reading,
                )
            )
        return TableReport(items=items, people_present=report.people_present)
