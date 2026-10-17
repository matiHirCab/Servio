"""Ids estables entre lecturas: empareja cajas nuevas con los platos anteriores (TOM-4)."""

import math
import re

from servio_vision.schemas import Item

# Una caja nueva es el mismo plato si su centro está a menos de esto × el lado de la caja.
MAX_SHIFT = 0.5
# Lecturas que se recuerda un plato que YOLO dejó de ver: si vuelve a aparecer en
# su lugar dentro de ese plazo, recupera su id en vez de recibir uno nuevo.
MAX_MISSING = 3


def _center(bbox) -> tuple[float, float]:
    x, y, w, h = bbox
    return x + w / 2, y + h / 2


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
      que YOLO lo dejó de ver, para devolverle su id si reaparece ahí.
    """

    def __init__(self) -> None:
        self._last: dict[str, int] = {}
        self._reading: dict[str, int] = {}
        self._where: dict[str, dict[str, tuple]] = {}  # mesa -> id -> (bbox, lectura)

    def first_new(self, table_id: str, previous_items: list[Item]) -> int:
        return max(self._last.get(table_id, 0) + 1, _next_number(previous_items))

    def remembered(self, table_id: str, previous_items: list[Item]) -> list[Item]:
        """Platos que faltaron hace poco y no están en `previous_items`."""
        present = {i.id for i in previous_items}
        now = self._reading.get(table_id, 0)
        return [
            Item(id=pid, position=position_label(bbox), bbox=bbox, visible=False)
            for pid, (bbox, reading) in self._where.get(table_id, {}).items()
            if pid not in present and now - reading <= MAX_MISSING
        ]

    def seen(self, table_id: str, ids: list[str], bboxes: list | None = None) -> None:
        nums = [int(m.group(1)) for i in ids if (m := re.fullmatch(r"p(\d+)", i))]
        self._last[table_id] = max([self._last.get(table_id, 0), *nums])
        now = self._reading[table_id] = self._reading.get(table_id, 0) + 1
        where = self._where.setdefault(table_id, {})
        for pid, bbox in zip(ids, bboxes or []):
            where[pid] = (bbox, now)
        for pid in [p for p, (_, r) in where.items() if now - r > MAX_MISSING]:
            del where[pid]


def match_ids(
    bboxes: list, previous_items: list[Item], first_new: int | None = None
) -> list[str]:
    """Id para cada caja ([x, y, w, h] normalizadas 0–1), en el mismo orden.

    Se empareja primero el par más cercano; una caja sin pareja cerca es un plato
    nuevo y recibe el siguiente `p{n}` desde `first_new` (por defecto, uno más que
    el mayor de `previous_items`; usar `IdCounter` para no reutilizar números).
    """
    candidates = []
    for i, b in enumerate(bboxes):
        cx, cy = _center(b)
        limit = MAX_SHIFT * max(b[2], b[3])
        for prev in previous_items:
            if prev.bbox is None:
                continue
            d = math.dist((cx, cy), _center(prev.bbox))
            if d <= limit:
                candidates.append((d, i, prev.id))
    ids: list[str | None] = [None] * len(bboxes)
    used = set()
    for _, i, pid in sorted(candidates):
        if ids[i] is None and pid not in used:
            ids[i] = pid
            used.add(pid)
    n = first_new or _next_number(previous_items)
    for i in range(len(ids)):
        if ids[i] is None:
            ids[i] = f"p{n}"
            n += 1
    return ids


def position_label(bbox) -> str:
    """ "arriba-izquierda", "centro", "abajo-derecha"… según dónde cae el centro."""
    cx, cy = _center(bbox)
    row = "arriba" if cy < 1 / 3 else "abajo" if cy > 2 / 3 else "centro"
    col = "izquierda" if cx < 1 / 3 else "derecha" if cx > 2 / 3 else "centro"
    if row == col == "centro":
        return "centro"
    return f"{row}-{col}"
