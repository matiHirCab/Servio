"""Clasificador `claude`: solo el modelo, con la mesa entera (sin YOLO de platos).

Es el respaldo del contrato (03-contratos.md) para cuando no hay detector de platos.
**Manda la imagen de la mesa al modelo**, lo que la decisión de privacidad C-3
(docs/legal-uruguay.md) sacó del camino normal (`hybrid` manda solo recortes de plato).
Por eso es opt-in y falla cerrado:

- hace falta `CLAUDE_ALLOW_TABLE_IMAGES=true` (por defecto no);
- hace falta el detector local de personas (`people.py`): lo que de ellas entre en la
  mesa va pixelado antes de salir. Las caras ya vienen difuminadas desde la captura
  (`capture.py`). Sin detector no se manda nada.

El modelo ubica cada plato en la grilla 3×3 de `tracking.position_label` y estima su
`fill_ratio`; los ids se siguen por posición (un plato que sigue en su celda conserva
su id). Anda con `cli` y `anthropic`; la Decisions API de OpenAI no puede encontrar
platos en una imagen entera.
"""

import io
import logging

from PIL import Image

from servio_vision import people, settings
from servio_vision.classifier import ClassifierUnavailable
from servio_vision.classifiers import claude_plates
from servio_vision.schemas import Item, TableReport

logger = logging.getLogger(__name__)

TABLE_SIDE = 1024  # px del lado mayor de la imagen que se manda


def _encode(img: Image.Image) -> bytes:
    img = img.copy()
    img.thumbnail((TABLE_SIDE, TABLE_SIDE))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    return buf.getvalue()


class ClaudeClassifier:
    def __init__(
        self,
        provider: str | None = None,
        model: str | None = None,
        timeout_s: float | None = None,
        person_detector: people.PersonDetector | None = None,
    ) -> None:
        if not settings.current().claude_allow_table_images:
            raise ClassifierUnavailable(
                "El clasificador claude manda la imagen de la mesa entera al modelo, y "
                "por privacidad (C-3, docs/legal-uruguay.md) hoy al modelo solo van "
                "recortes de plato. Si el equipo decidió permitirlo, poné "
                "CLAUDE_ALLOW_TABLE_IMAGES=true; si no, usá --classifier hybrid."
            )
        self.people = person_detector or people.load_detector()
        if self.people is None:
            raise ClassifierUnavailable(
                "Sin el detector de personas no se manda la mesa entera: no hay cómo "
                "pixelar a los comensales. pip install -r requirements-yolo.txt"
            )
        self.claude_kwargs = {
            "provider": provider,
            "model": model,
            "timeout_s": timeout_s,
        }
        self._next_id: dict[str, int] = {}  # por mesa; el número solo sube

    def classify(
        self, image: bytes, table_id: str, previous_items: list[Item]
    ) -> TableReport | None:
        img = Image.open(io.BytesIO(image)).convert("RGB")
        persons = self.people.detect(img)
        # lo que de las personas entre en la mesa sale pixelado, no la mesa cruda
        safe = people.anonymize_crop(img, (0.0, 0.0), persons)
        estimate = claude_plates.estimate_table(
            _encode(safe), table_id, **self.claude_kwargs
        )
        if estimate is None:
            return None
        by_position = {i.position: i.id for i in previous_items}
        items: list[Item] = []
        for plate in estimate.plates:
            # un plato que sigue en su celda conserva el id; si no, uno nuevo
            plate_id = by_position.get(plate.position) or self._new_id(
                table_id, previous_items
            )
            if plate.visible:
                items.append(
                    Item(
                        id=plate_id,
                        position=plate.position,
                        fill_ratio=plate.fill_ratio,
                        confidence=plate.confidence,
                    )
                )
            else:
                items.append(
                    Item(
                        id=plate_id,
                        position=plate.position,
                        confidence=plate.confidence,
                        visible=False,
                    )
                )
        # lo que vio el detector local o lo que infiere el modelo (como en `hybrid`)
        return TableReport(
            items=items, people_present=bool(persons) or estimate.people_present
        )

    def _new_id(self, table_id: str, previous_items: list[Item]) -> str:
        used = [
            int(i.id[1:])
            for i in previous_items
            if i.id.startswith("p") and i.id[1:].isdigit()
        ]
        number = max(self._next_id.get(table_id, 1), max(used, default=0) + 1)
        self._next_id[table_id] = number + 1
        return f"p{number}"
