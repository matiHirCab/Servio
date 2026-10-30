"""Clasificador `hybrid` (TOM-5), el de la demo: YOLO encuentra los platos y Claude
estima cuánta comida queda en cada uno.

1. YOLO detecta los platos en el recorte de la mesa y se emparejan los ids.
2. Un plato que YOLO dejó de ver se sigue mandando a Claude con su última caja mientras
   `IdCounter` lo recuerda (`MAX_MISSING` lecturas): si Claude lo ve, sigue con su id
   y su lectura, y se lo recuerda de nuevo. Si su caja se pisa con una que YOLO sí vio,
   es el mismo plato con otro id y no se manda.
3. Cada plato se recorta con 10 % de margen. Lo que entre de una persona en el
   recorte (una mano, un brazo) va pixelado: las personas las encuentra en la máquina
   `people.py` (C-3). **La imagen de la mesa no se manda**: al modelo van solo los
   recortes, y `people_present` sale del detector local (si no hay, del modelo).
4. Una llamada a Claude por mesa (`claude_plates.estimate_plates`) con los platos;
   cada plato que ya tiene referencia (su recorte recién servido) la lleva al lado.
5. Si Claude falla, usa el estado de la clase de YOLO con confianza más baja; si el
   detector no da estado (YOLOE sin entrenar), la lectura se descarta (`None`).

`classify` hace todo junto. Para correr varias lecturas a la vez está partido en
`prepare` (YOLO e ids, en orden), `ask` (Claude, se puede llamar en paralelo) y
`finish` (arma el reporte, en orden).
"""

import io
import logging
import threading
from dataclasses import dataclass

import numpy as np
from PIL import Image

from servio_vision import people, settings
from servio_vision.classifiers import claude_plates
from servio_vision.classifiers.yolo import (
    FoundPlate,
    decode,
    find_plates,
    items_from_labels,
)
from servio_vision.detector import PlateDetector
from servio_vision.schemas import EMPTY_MAX, FULL_MIN, Item, TableReport
from servio_vision.tracking import IdCounter, bbox_iou, position_label

logger = logging.getLogger(__name__)

CROP_MARGIN = 0.10
CROP_SIDE = 384  # px del lado mayor de cada recorte de plato
# Lecturas seguidas sin ver la comida para mostrar un plato como «tapado». Antes de
# eso conserva su última lectura (un brazo que pasa no lo tapa).
HIDDEN_READINGS = 3
FALLBACK_CONFIDENCE = 0.5  # la confianza de YOLO se multiplica por esto si Claude falla
# Un plato que YOLO no encuentra se le manda a Claude solo mientras `IdCounter` lo
# recuerda (MAX_MISSING lecturas). Si se lo llevaron, Claude podría leer el hueco como
# un plato vacío: por eso una lectura de un plato perdido que dice vacío o que baja más
# que esto de golpe no se toma como vista.
LOST_MAX_DROP = 0.3
# Un plato perdido cuya caja se pisa más que esto con una que YOLO vio en esta lectura
# es ese mismo plato con otro id: mandarlo daría dos platos donde hay uno.
LOST_MAX_IOU = 0.3
# Lecturas seguidas que se sigue recordando un plato perdido que Claude ve. Con un tope:
# si Claude se equivoca con el hueco, el plato fantasma no queda para siempre.
LOST_KEEP_MAX = 10
# Un plato que YOLO deja de ver y cuya última lectura de Claude era menos que esto
# (vacío o restos) no se le manda a Claude: se da por retirado.
LOST_EMPTY_FILL = 0.3
# Referencia: el recorte de un plato recién servido, que va a Claude junto al actual para
# que mida contra lo que se sirvió. Se toma solo en las primeras lecturas visibles del plato
# y si Claude lo ve lleno: un id nuevo a mitad de comida (p. ej., tras perder el plato) no
# adopta como «lleno» un plato medio comido. Si Claude dice que la referencia es de otra
# comida (`same_plate=False`, ids cambiados), se descarta.
REFERENCE_READINGS = 3
# Una mesa que no cambió no se vuelve a mandar al modelo (la mayor parte del costo es
# preguntar lo mismo): se reutiliza su última respuesta mientras cada plato se vea casi
# igual al recorte que se mandó (diferencia media de brillo en una miniatura de
# THUMB×THUMB, de 0 a 255; el ruido de la cámara da ~1) y, aun así, cada `MAX_REUSES`
# lecturas seguidas se pregunta de nuevo, por si un cambio chico no pasó el umbral.
# Un plato que cambia, llega o se va, o gente que entra o sale, siempre se pregunta.
MAX_REUSES = 3
REUSE_MAX_DIFF = 5.0
THUMB = 24


def _thumb(jpeg: bytes) -> np.ndarray:
    """Miniatura en gris del recorte: promedia el ruido de la cámara y deja lo que
    cambió de verdad (una porción menos, una mano)."""
    img = Image.open(io.BytesIO(jpeg)).convert("L")
    return np.asarray(
        img.resize((THUMB, THUMB), Image.Resampling.BOX), dtype=np.float32
    )


class ReusedReport(claude_plates.PlatesReport):
    """La respuesta de una lectura anterior, reutilizada porque la mesa no cambió.
    No es evidencia nueva: `finish` no la cuenta como una lectura más (ver
    `HybridClassifier._carry_over`), si no una sola respuesta, quizá equivocada,
    bastaría para que el suavizado acepte un cambio de estado."""


@dataclass
class _Asked:
    """Lo que se le preguntó al modelo por última vez en una mesa."""

    ids: tuple[str, ...]
    thumbs: list[np.ndarray]
    people_present: bool | None
    report: claude_plates.PlatesReport
    reused: int = 0  # lecturas seguidas que se reutilizó esta respuesta


def _jpeg(img: Image.Image, side: int) -> bytes:
    img = img.copy()
    img.thumbnail((side, side))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    return buf.getvalue()


def _crop_box(img: Image.Image, bbox) -> tuple[float, float, float, float]:
    """(left, top, right, bottom) del recorte del plato, con margen, en píxeles."""
    w, h = img.size
    x, y, bw, bh = bbox[0] * w, bbox[1] * h, bbox[2] * w, bbox[3] * h
    mx, my = CROP_MARGIN * bw, CROP_MARGIN * bh
    return (max(0, x - mx), max(0, y - my), min(w, x + bw + mx), min(h, y + bh + my))


def _crop(
    img: Image.Image, bbox, persons: list[people.Box] | None = None
) -> Image.Image:
    """El recorte del plato; con `persons` (cajas en la imagen de la mesa), lo que
    de ellas entre en el recorte va pixelado."""
    box = _crop_box(img, bbox)
    crop = img.crop(box)
    return people.anonymize_crop(crop, (box[0], box[1]), persons or [])


def _looks_taken(fill_ratio: float, last_fill: float | None) -> bool:
    """Lectura de un plato que YOLO perdió que parece el hueco donde estaba.

    `last_fill` es la última lectura visible de Claude, no la suavizada: contra la
    suavizada, un plato que se está comiendo parece caer de golpe."""
    if fill_ratio < EMPTY_MAX:
        return True
    return last_fill is not None and last_fill - fill_ratio > LOST_MAX_DROP


@dataclass
class Prepared:
    """Lo que sale de `prepare`: los platos a leer y las imágenes para Claude."""

    table_id: str
    plates: list[FoundPlate]  # primero los que vio YOLO, después los perdidos
    lost: set[str]  # ids de los platos que YOLO no vio en esta lectura
    plate_jpegs: list[bytes]  # ya con las personas pixeladas
    references: list[bytes | None]  # por plato, su recorte recién servido
    people_present: bool | None = None  # del detector local; None si no hay

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
        person_detector: people.PersonDetector | None = None,
        reuse_unchanged: bool | None = None,
        **detector_kwargs,
    ):
        """`reuse_unchanged`: no volver a preguntar por una mesa que no cambió (ver
        `MAX_REUSES`); sin valor, el de la configuración (`CLAUDE_REUSE_UNCHANGED`)."""
        self.detector = detector or PlateDetector(**detector_kwargs)
        self.reuse_unchanged = (
            settings.current().claude_reuse_unchanged
            if reuse_unchanged is None
            else reuse_unchanged
        )
        self._asked: dict[str, _Asked] = {}  # por mesa; cada mesa en su hilo
        self._asked_lock = threading.Lock()
        # personas en la mesa: para pixelarlas en los recortes y saber si hay gente
        # sin mandar la mesa al modelo (C-3)
        self.people = person_detector or people.load_detector()
        self.ids = IdCounter()
        self.people_present: dict[str, bool] = {}  # última lectura, por mesa
        self.hidden: dict[tuple[str, str], int] = {}  # lecturas seguidas sin verlo
        self.last_fill: dict[tuple[str, str], float] = {}  # última lectura visible
        self.lost_readings: dict[tuple[str, str], int] = {}  # seguidas sin YOLO
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
        plates = find_plates(self.detector, img, previous_items, self.ids, table_id)
        lost = [
            FoundPlate(pid, bbox, None)
            for pid, bbox in self.ids.missing(table_id)
            if all(bbox_iou(bbox, p.bbox) <= LOST_MAX_IOU for p in plates)
            # vacío o con restos y YOLO ya no lo ve: lo retiraron. Si se le pregunta
            # a Claude, lee el hueco como "plato vacío" y queda un fantasma.
            and self.last_fill.get((table_id, pid), 1.0) >= LOST_EMPTY_FILL
        ]
        everything = plates + lost
        # personas solo si hay algo que mandar: en una mesa vacía no se gasta YOLO
        persons = (
            self.people.detect(img) if everything and self.people is not None else None
        )
        return Prepared(
            table_id=table_id,
            plates=everything,
            lost={p.id for p in lost},
            plate_jpegs=[
                _jpeg(_crop(img, p.bbox, persons), CROP_SIDE) for p in everything
            ],
            references=[self.references.get((table_id, p.id)) for p in everything],
            people_present=None if persons is None else bool(persons),
        )

    def ask(self, prepared: Prepared) -> claude_plates.PlatesReport | None:
        """La llamada a Claude: solo los recortes de plato, nunca la mesa (C-3). No
        toca el estado del clasificador."""
        if not prepared.plates:
            return claude_plates.PlatesReport(plates=[], people_present=True)
        if self.reuse_unchanged:
            reused = self._reusable(prepared)
            if reused is not None:
                logger.debug(
                    "Mesa %s sin cambios: se reutiliza la lectura", prepared.table_id
                )
                return reused
        report = claude_plates.estimate_plates(
            prepared.plate_jpegs,
            prepared.table_id,
            references=prepared.references,
            **self.claude_kwargs,
        )
        if self.reuse_unchanged:
            self._remember(prepared, report)
        return report

    def _reusable(self, prepared: Prepared) -> claude_plates.PlatesReport | None:
        """La última respuesta del modelo para esta mesa, si nada cambió; `None` si hay
        que volver a preguntar. Conservador: ante cualquier duda, se pregunta."""
        with self._asked_lock:
            asked = self._asked.get(prepared.table_id)
        ids = tuple(p.id for p in prepared.plates)
        if (
            asked is None
            or asked.reused >= MAX_REUSES
            or prepared.lost  # un plato que YOLO perdió: caso delicado, se pregunta
            or asked.ids != ids
            or asked.people_present != prepared.people_present
            # un plato nuevo todavía arma su referencia con las primeras lecturas
            or any(
                self.readings.get((prepared.table_id, i), 0) < REFERENCE_READINGS
                for i in ids
            )
        ):
            return None
        thumbs = [_thumb(jpeg) for jpeg in prepared.plate_jpegs]
        if any(
            float(np.abs(old - new).mean()) > REUSE_MAX_DIFF
            for old, new in zip(asked.thumbs, thumbs, strict=True)
        ):
            return None
        asked.reused += 1
        return ReusedReport(
            plates=asked.report.plates, people_present=asked.report.people_present
        )

    def _remember(
        self, prepared: Prepared, report: claude_plates.PlatesReport | None
    ) -> None:
        """Guarda lo que se preguntó; sin respuesta no queda nada que reutilizar."""
        with self._asked_lock:
            if report is None:
                self._asked.pop(prepared.table_id, None)
                return
            self._asked[prepared.table_id] = _Asked(
                ids=tuple(p.id for p in prepared.plates),
                thumbs=[_thumb(jpeg) for jpeg in prepared.plate_jpegs],
                people_present=prepared.people_present,
                report=report,
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
        if isinstance(report, ReusedReport):
            carried = self._carry_over(prepared, previous_items)
            if carried is not None:
                return carried
        # Lo que vio el detector local **o** lo que infiere el modelo de los recortes
        # (manos, cubiertos en uso): desde arriba, los comensales quedan casi fuera
        # del recorte de la mesa y el detector solo diría "nadie" con gente sentada,
        # y eso cierra avisos de plato lleno (engine.py). Si Claude falló, el detector
        # solo suma: un "nadie" suyo no pisa la última lectura buena.
        local = prepared.people_present
        if report is not None:
            self.people_present[table_id] = bool(local) or report.people_present
        elif local:
            self.people_present[table_id] = True
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
        last = {i.id: i for i in previous_items if i.state is not None}
        items = []
        for p, est, crop, ref in zip(
            prepared.plates, report.plates, prepared.plate_jpegs, prepared.references
        ):
            key = (table_id, p.id)
            visible = est.visible
            if p.id in prepared.lost:
                n = self.lost_readings[key] = self.lost_readings.get(key, 0) + 1
                if visible:
                    visible = not _looks_taken(est.fill_ratio, self.last_fill.get(key))
                if visible and n < LOST_KEEP_MAX:
                    self.ids.keep(table_id, p.id)
            else:
                self.lost_readings.pop(key, None)
            self.hidden[key] = 0 if visible else self.hidden.get(key, 0) + 1
            if visible and p.id not in prepared.lost:
                self._update_reference(key, est, crop, sent=ref is not None)
            if visible:
                self.last_fill[key] = est.fill_ratio
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
        return TableReport(items=items, people_present=self.people_present[table_id])

    def _carry_over(
        self, prepared: Prepared, previous_items: list[Item]
    ) -> TableReport | None:
        """La mesa tal como estaba, sin lectura nueva: cada plato con su último estado
        y `visible=False`, que el suavizado no cuenta (como un plato tapado). No toca
        la memoria del clasificador (referencias, lecturas, `hidden`). `None` si algún
        plato no tiene todavía un estado: entonces va como lectura normal."""
        last = {i.id: i for i in previous_items if i.state is not None}
        if any(p.id not in last for p in prepared.plates):
            return None
        return TableReport(
            items=[
                Item(
                    id=p.id,
                    position=position_label(p.bbox),
                    bbox=p.bbox,
                    fill_ratio=last[p.id].fill_ratio,
                    state=last[p.id].state,
                    confidence=last[p.id].confidence,
                    visible=False,
                )
                for p in prepared.plates
            ],
            people_present=self.people_present.get(prepared.table_id, True),
        )

    def _update_reference(
        self, key, est: claude_plates.PlateEstimate, crop: bytes, sent: bool
    ):
        # `same_plate` solo dice algo si esta lectura llevó la referencia: con lecturas
        # en paralelo, la referencia puede haberse guardado después de preparar esta
        if sent and not est.same_plate and self.references.pop(key, None) is not None:
            logger.info(
                "Mesa %s %s: la referencia es de otra comida, se descarta", *key
            )
            # no toma otra: lo de ahora puede ser un plato medio comido que se ve lleno
            self.readings[key] = REFERENCE_READINGS
        n = self.readings[key] = self.readings.get(key, 0) + 1
        if (
            key not in self.references
            and n <= REFERENCE_READINGS
            and est.fill_ratio >= FULL_MIN
        ):
            self.references[key] = crop
