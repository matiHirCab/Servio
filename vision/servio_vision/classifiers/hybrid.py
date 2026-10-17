"""Clasificador `hybrid` (TOM-5), el de la demo: YOLO encuentra los platos y Claude
estima cuánta comida queda en cada uno.

1. YOLO detecta los platos en el recorte de la mesa y se emparejan los ids.
2. Un plato que YOLO dejó de ver se sigue mandando a Claude con su última caja mientras
   `IdCounter` lo recuerda (`MAX_MISSING` lecturas): si Claude lo ve, sigue con su id
   y su lectura.
3. Cada plato se recorta con 10 % de margen.
4. Una llamada a Claude por mesa (`claude_plates.estimate_plates`) con la mesa y los platos;
   cada plato que ya tiene referencia (su recorte recién servido) la lleva al lado.
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
from servio_vision.schemas import EMPTY_MAX, FULL_MIN, Item, TableReport
from servio_vision.tracking import IdCounter, position_label

logger = logging.getLogger(__name__)

CROP_MARGIN = 0.10
CROP_SIDE = 384  # px del lado mayor de cada recorte de plato
TABLE_SIDE = 1024  # px del lado mayor de la mesa que ve Claude
# Lecturas seguidas sin ver la comida para mostrar un plato como «tapado». Antes de
# eso conserva su última lectura (un brazo que pasa no lo tapa).
HIDDEN_READINGS = 3
FALLBACK_CONFIDENCE = 0.5  # la confianza de YOLO se multiplica por esto si Claude falla
# Un plato que YOLO no encuentra se le manda a Claude solo mientras `IdCounter` lo
# recuerda (MAX_MISSING lecturas). Si se lo llevaron, Claude podría leer el hueco como
# un plato vacío: por eso una lectura de un plato perdido que dice vacío o que baja más
# que esto de golpe no se toma como vista.
LOST_MAX_DROP = 0.3
# Referencia: el recorte de un plato recién servido, que va a Claude junto al actual para
# que mida contra lo que se sirvió. Se toma solo en las primeras lecturas visibles del plato
# y si Claude lo ve lleno: un id nuevo a mitad de comida (p. ej., tras perder el plato) no
# adopta como «lleno» un plato medio comido. Si Claude dice que la referencia es de otra
# comida (`same_plate=False`, ids cambiados), se descarta.
REFERENCE_READINGS = 3


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


def _looks_taken(fill_ratio: float, last: Item | None) -> bool:
    """Lectura de un plato que YOLO perdió que parece el hueco donde estaba."""
    if fill_ratio < EMPTY_MAX:
        return True
    return (
        last is not None
        and last.fill_ratio is not None
        and last.fill_ratio - fill_ratio > LOST_MAX_DROP
    )


@dataclass
class Prepared:
    """Lo que sale de `prepare`: los platos a leer y las imágenes para Claude."""

    table_id: str
    plates: list[FoundPlate]  # primero los que vio YOLO, después los perdidos
    lost: set[str]  # ids de los platos que YOLO no vio en esta lectura
    table_jpeg: bytes
    plate_jpegs: list[bytes]
    references: list[bytes | None]  # por plato, su recorte recién servido

    @property
    def seen_items(self) -> list[Item]:
        """Los platos que vio YOLO, como `previous_items` para preparar la próxima
        lectura sin esperar a Claude."""
        return [
            Item(id=p.id, position=position_label(p.bbox), bbox=p.bbox, visible=False)
            for p in self.plates
            if p.id not in self.lost
        ]


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
        self.lost: dict[str, set[str]] = {}  # por mesa, perdidos en la última lectura
        self.references: dict[tuple[str, str], bytes] = {}  # ver REFERENCE_READINGS
        self.readings: dict[tuple[str, str], int] = {}  # lecturas visibles por plato
        self.claude_kwargs = {
            "provider": provider,
            "model": model,
            "timeout_s": timeout_s,
        }

    def classify(
        self, image: bytes, table_id: str, previous_items: list[Item]
    ) -> TableReport | None:
        prepared = self.prepare(image, table_id, previous_items)
        return self.finish(prepared, self.ask(prepared), previous_items)

    def prepare(
        self, image: bytes, table_id: str, previous_items: list[Item]
    ) -> Prepared:
        """YOLO, ids y recortes. No usa lo que dijo Claude, así la lectura siguiente
        puede prepararse antes de que Claude conteste esta (con `seen_items` de la
        anterior como `previous_items`)."""
        img = decode(image)
        # los perdidos traen su caja vieja: emparejan desde la memoria de IdCounter,
        # con menos prioridad que los que YOLO vio en la lectura anterior
        stale = self.lost.get(table_id, set())
        previous = [i for i in previous_items if i.id not in stale]
        plates = find_plates(self.detector, img, previous, self.ids, table_id)
        lost = [FoundPlate(pid, bbox, None) for pid, bbox in self.ids.missing(table_id)]
        self.lost[table_id] = {p.id for p in lost}
        everything = plates + lost
        return Prepared(
            table_id=table_id,
            plates=everything,
            lost=self.lost[table_id],
            table_jpeg=_jpeg(img, TABLE_SIDE) if everything else b"",
            plate_jpegs=[_jpeg(_crop(img, p.bbox), CROP_SIDE) for p in everything],
            references=[self.references.get((table_id, p.id)) for p in everything],
        )

    def ask(self, prepared: Prepared) -> claude_plates.PlatesReport | None:
        """La llamada a Claude. No toca el estado del clasificador."""
        if not prepared.plates:
            return claude_plates.PlatesReport(plates=[], people_present=True)
        return claude_plates.estimate_plates(
            prepared.table_jpeg,
            prepared.plate_jpegs,
            prepared.table_id,
            references=prepared.references,
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
        for p, est, crop in zip(prepared.plates, report.plates, prepared.plate_jpegs):
            key = (table_id, p.id)
            visible = est.visible
            if visible and p.id in prepared.lost:
                visible = not _looks_taken(est.fill_ratio, last.get(p.id))
            self.hidden[key] = 0 if visible else self.hidden.get(key, 0) + 1
            if visible and p.id not in prepared.lost:
                self._update_reference(key, est, crop)
            if visible:
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
                self.hidden.pop(key, None)
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

    def _update_reference(self, key, est: claude_plates.PlateEstimate, crop: bytes):
        if not est.same_plate and self.references.pop(key, None) is not None:
            logger.info("Mesa %s %s: la referencia es de otra comida, se descarta", *key)
            # no toma otra: lo de ahora puede ser un plato medio comido que se ve lleno
            self.readings[key] = REFERENCE_READINGS
        n = self.readings[key] = self.readings.get(key, 0) + 1
        if (
            key not in self.references
            and n <= REFERENCE_READINGS
            and est.fill_ratio >= FULL_MIN
        ):
            self.references[key] = crop
