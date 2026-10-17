"""Clasificador `hybrid` (TOM-5), el de la demo: YOLO encuentra los platos y Claude
estima cuánta comida queda en cada uno.

1. YOLO detecta los platos en el recorte de la mesa y se emparejan los ids.
2. Un plato que YOLO dejó de ver se sigue mandando a Claude con su última caja, hasta
   `LOST_READINGS` lecturas: si Claude lo ve, sigue con su id y su lectura.
3. Cada plato se recorta con 10 % de margen.
4. Una llamada a Claude por mesa (`claude_plates.estimate_plates`) con la mesa y los platos.
5. Si Claude falla, usa el estado de la clase de YOLO con confianza más baja; si el
   detector no da estado (YOLOE sin entrenar), la lectura se descarta (`None`).

`classify` hace todo junto. Para correr varias lecturas a la vez está partido en
`prepare` (YOLO e ids, en orden), `ask` (Claude, se puede llamar en paralelo) y
`finish` (arma el reporte, en orden).
"""

import io
import logging
from dataclasses import dataclass

from PIL import Image

from servio_vision.classifiers import claude_plates
from servio_vision.classifiers.yolo import (
    FoundPlate,
    decode,
    find_plates,
    items_from_labels,
)
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
# Lecturas seguidas que se le sigue mandando a Claude un plato que YOLO no encuentra.
# Corto a propósito: si se llevaron el plato, Claude podría leer el hueco como vacío.
LOST_READINGS = 3


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


@dataclass
class Prepared:
    """Lo que sale de `prepare`: los platos a leer y las imágenes para Claude."""

    table_id: str
    plates: list[FoundPlate]  # primero los que vio YOLO, después los perdidos
    lost: set[str]  # ids de los platos que YOLO no vio en esta lectura
    table_jpeg: bytes
    plate_jpegs: list[bytes]


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
        # por mesa: id -> (última caja, lecturas seguidas que YOLO no lo encontró)
        self.known: dict[str, dict[str, tuple[tuple, int]]] = {}
        self.claude_kwargs = {
            "provider": provider,
            "model": model,
            "timeout_s": timeout_s,
        }

    def classify(
        self, image: bytes, table_id: str, previous_items: list[Item]
    ) -> TableReport | None:
        prepared = self.prepare(image, table_id)
        return self.finish(prepared, self.ask(prepared), previous_items)

    def prepare(self, image: bytes, table_id: str) -> Prepared:
        """YOLO, ids y recortes. Usa solo lo que vio YOLO antes, no lo que dijo
        Claude, así la lectura siguiente puede prepararse antes de que Claude
        conteste esta."""
        img = decode(image)
        known = self.known.setdefault(table_id, {})
        previous = [
            Item(id=pid, position=position_label(bbox), bbox=bbox, visible=False)
            for pid, (bbox, _) in known.items()
        ]
        plates = find_plates(self.detector, img, previous, self.ids, table_id)
        seen = {p.id for p in plates}
        lost = []
        for pid, (bbox, missing) in list(known.items()):
            if pid in seen:
                continue
            if missing + 1 > LOST_READINGS:
                del known[pid]
                continue
            known[pid] = (bbox, missing + 1)
            lost.append(FoundPlate(pid, bbox, None))
        for p in plates:
            known[p.id] = (p.bbox, 0)
        everything = plates + lost
        return Prepared(
            table_id=table_id,
            plates=everything,
            lost={p.id for p in lost},
            table_jpeg=_jpeg(img, TABLE_SIDE) if everything else b"",
            plate_jpegs=[_jpeg(_crop(img, p.bbox), CROP_SIDE) for p in everything],
        )

    def ask(self, prepared: Prepared) -> claude_plates.PlatesReport | None:
        """La llamada a Claude. No toca el estado del clasificador."""
        if not prepared.plates:
            return claude_plates.PlatesReport(plates=[], people_present=True)
        return claude_plates.estimate_plates(
            prepared.table_jpeg,
            prepared.plate_jpegs,
            prepared.table_id,
            **self.claude_kwargs,
        )

    def finish(
        self,
        prepared: Prepared,
        report: claude_plates.PlatesReport | None,
        previous_items: list[Item],
    ) -> TableReport | None:
        table_id = prepared.table_id
        if not prepared.plates:
            return TableReport(items=[])
        if report is None:
            # sin Claude no hay cómo saber si un plato perdido sigue ahí: se omite
            detected = [p for p in prepared.plates if p.id not in prepared.lost]
            items = items_from_labels(detected, FALLBACK_CONFIDENCE)
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
        for p, est in zip(prepared.plates, report.plates):
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
            elif p.id in prepared.lost:
                continue  # ni YOLO ni Claude lo ven: no se muestra
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
