"""Ids estables entre lecturas: empareja cajas nuevas con los platos anteriores (TOM-4)."""

import math
import re

from servio_vision.detector import _iou
from servio_vision.schemas import Item

# Una caja nueva es el mismo plato si su centro está a menos de esto × el lado de la caja.
MAX_SHIFT = 0.5
# Lecturas que se recuerda un plato que YOLO dejó de ver: si vuelve a aparecer en
# su lugar dentro de ese plazo, recupera su id en vez de recibir uno nuevo.
MAX_MISSING = 3


def _center(bbox) -> tuple[float, float]:
    x, y, w, h = bbox
    return x + w / 2, y + h / 2


def bbox_iou(a, b) -> float:
    """IoU de dos cajas [x, y, w, h]."""
    return _iou(
        (a[0], a[1], a[0] + a[2], a[1] + a[3]), (b[0], b[1], b[0] + b[2], b[1] + b[3])
    )


def _next_number(previous_items: list[Item]) -> int:
    nums = [
        int(m.group(1)) for i in previous_items if (m := re.fullmatch(r"p(\d+)", i.id))
    ]
    return max(nums, default=0) + 1


class IdCounter:
    """Memoria de ids por mesa.

    - El número de id solo sube: un plato que faltó en una lectura no le deja su
      número a uno nuevo (el suavizado todavía guarda su pista).
    - Recuerda dónde estaba cada plato hasta `MAX_MISSING` lecturas después de
      que YOLO lo dejó de ver, para devolverle su id si reaparece ahí. Los platos
      de la lectura anterior tienen prioridad sobre los recordados.
    """

    def __init__(self) -> None:
        self._last: dict[str, int] = {}
        self._reading: dict[str, int] = {}
        self._where: dict[str, dict[str, tuple]] = {}  # mesa -> id -> (bbox, lectura)

    def assign(
        self, table_id: str, bboxes: list, previous_items: list[Item]
    ) -> list[str]:
        """Ids para las cajas de esta lectura, y actualiza la memoria."""
        # los que YOLO no vio en la última `assign` van con los recordados, aunque
        # el que llama todavía los tenga en `previous_items`
        lost = {pid for pid, _ in self.missing(table_id)}
        previous = [
            (i.id, i.bbox)
            for i in previous_items
            if i.bbox is not None and i.id not in lost
        ]
        present = {pid for pid, _ in previous}
        remembered = [
            (pid, bbox)
            for pid, (bbox, _) in self._where.get(table_id, {}).items()
            if pid not in present
        ]
        first_new = max(self._last.get(table_id, 0) + 1, _next_number(previous_items))
        ids = _match(bboxes, [previous, remembered], first_new)

        nums = [int(m.group(1)) for i in ids if (m := re.fullmatch(r"p(\d+)", i))]
        self._last[table_id] = max([self._last.get(table_id, 0), *nums])
        now = self._reading[table_id] = self._reading.get(table_id, 0) + 1
        where = self._where.setdefault(table_id, {})
        for pid, bbox in zip(ids, bboxes):
            where[pid] = (bbox, now)
        for pid in [p for p, (_, r) in where.items() if now - r > MAX_MISSING]:
            del where[pid]
        return ids

    def missing(self, table_id: str) -> list[tuple[str, tuple]]:
        """(id, última caja) de los platos que YOLO no vio en la última `assign` de
        esa mesa pero todavía se recuerdan (hasta `MAX_MISSING` lecturas)."""
        now = self._reading.get(table_id, 0)
        return [
            (pid, bbox)
            for pid, (bbox, r) in self._where.get(table_id, {}).items()
            if r < now
        ]

    def remap(self, table_id: str, remap) -> None:
        """La mesa se movió: las cajas recordadas pasan al recorte nuevo (si no, un
        plato perdido volvería con la caja del recorte viejo, corrida)."""
        where = self._where.get(table_id, {})
        for pid, (bbox, r) in list(where.items()):
            moved = remap(bbox)
            if moved is None:
                del where[pid]  # quedó fuera de la mesa: no se busca más
            else:
                where[pid] = (moved, r)

    def keep(self, table_id: str, pid: str) -> None:
        """Otro (Claude) confirmó que un plato perdido sigue ahí: se lo recuerda
        `MAX_MISSING` lecturas más, sin dejar de ser un perdido."""
        where = self._where.get(table_id, {})
        if pid in where:
            where[pid] = (where[pid][0], self._reading[table_id] - 1)


def _match(bboxes: list, tiers: list[list[tuple]], first_new: int) -> list[str]:
    """Empareja cada caja con un (id, bbox) cercano, tier por tier: las cajas que no
    encuentran pareja en un tier prueban con el siguiente. Dentro de un tier va
    primero el par más cercano. Lo que queda sin pareja recibe `p{first_new}`, …"""
    ids: list[str | None] = [None] * len(bboxes)
    used: set[str] = set()
    for tier in tiers:
        candidates = []
        for i, b in enumerate(bboxes):
            if ids[i] is not None:
                continue
            cx, cy = _center(b)
            limit = MAX_SHIFT * max(b[2], b[3])
            for pid, pb in tier:
                d = math.dist((cx, cy), _center(pb))
                if d <= limit:
                    candidates.append((d, i, pid))
        for _, i, pid in sorted(candidates):
            if ids[i] is None and pid not in used:
                ids[i] = pid
                used.add(pid)
    n = first_new
    for i in range(len(ids)):
        if ids[i] is None:
            ids[i] = f"p{n}"
            n += 1
    return ids


def match_ids(
    bboxes: list, previous_items: list[Item], first_new: int | None = None
) -> list[str]:
    """Id para cada caja ([x, y, w, h] normalizadas 0–1), en el mismo orden.

    Se empareja primero el par más cercano; una caja sin pareja cerca es un plato
    nuevo y recibe el siguiente `p{n}` desde `first_new` (por defecto, uno más que
    el mayor de `previous_items`). Sin memoria: para eso, `IdCounter.assign`.
    """
    previous = [(i.id, i.bbox) for i in previous_items if i.bbox is not None]
    return _match(bboxes, [previous], first_new or _next_number(previous_items))


# Las nueve etiquetas posibles de `position_label` (la grilla 3×3 de la mesa).
POSITION_LABELS = (
    "arriba-izquierda",
    "arriba-centro",
    "arriba-derecha",
    "centro-izquierda",
    "centro",
    "centro-derecha",
    "abajo-izquierda",
    "abajo-centro",
    "abajo-derecha",
)


def position_label(bbox) -> str:
    """ "arriba-izquierda", "centro", "abajo-derecha"… según dónde cae el centro."""
    cx, cy = _center(bbox)
    row = "arriba" if cy < 1 / 3 else "abajo" if cy > 2 / 3 else "centro"
    col = "izquierda" if cx < 1 / 3 else "derecha" if cx > 2 / 3 else "centro"
    if row == col == "centro":
        return "centro"
    return f"{row}-{col}"
