"""Modelos de la herramienta `report_table` (contrato P1 ↔ P3).

Fuente de verdad: docs/context/03-contratos.md («Visión interna») y
docs/context/04-vision.md («Prompt»). Cambios acá van en un PR revisado por P3.
"""

import logging
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

logger = logging.getLogger(__name__)

ItemKind = Literal["plate"]  # solo platos de comida: bebidas fuera de alcance
PlateState = Literal["lleno", "comiendo", "por_terminar", "vacio"]

# Umbrales de fill_ratio (fracción de comida que queda) entre estados.
FULL_MIN = 0.75  # >= 0.75 → lleno
EATING_MIN = 0.30  # 0.30–0.75 → comiendo (no dispara avisos)
EMPTY_MAX = 0.05  # 0.05–0.30 → por_terminar · < 0.05 → vacio


def state_from_fill_ratio(fill_ratio: float) -> PlateState:
    """Estado de un plato según cuánta comida le queda (0 a 1)."""
    if fill_ratio >= FULL_MIN:
        return "lleno"
    if fill_ratio >= EATING_MIN:
        return "comiendo"
    if fill_ratio >= EMPTY_MAX:
        return "por_terminar"
    return "vacio"


class Item(BaseModel):
    """Un plato sobre la mesa, tal como lo devuelve el clasificador.

    Si viene `fill_ratio`, el estado sale de ahí (`state_from_fill_ratio`) y
    pisa el `state` que haya mandado el modelo, para que nunca se contradigan.
    Un plato tapado (`visible=False`) puede venir sin `state` ni `confidence`.
    """

    id: str = Field(min_length=1, description='Id estable entre lecturas, ej. "p1".')
    kind: ItemKind = "plate"
    state: PlateState | None = None
    fill_ratio: float | None = Field(
        default=None, ge=0, le=1, description="Fracción de comida que queda."
    )
    position: str = Field(description='Texto libre, ej. "arriba-izquierda".')
    bbox: tuple[float, float, float, float] | None = Field(
        default=None,
        description="[x, y, w, h] normalizado 0–1 dentro del recorte de la mesa (lo da YOLO).",
    )
    confidence: float | None = Field(default=None, ge=0, le=1)
    visible: bool = Field(
        default=True,
        description="False si algo lo tapa: no cuenta como lectura en el suavizado.",
    )

    @model_validator(mode="after")
    def _state_matches_fill_ratio(self) -> "Item":
        if self.fill_ratio is not None:
            self.state = state_from_fill_ratio(self.fill_ratio)
        if self.visible and (self.state is None or self.confidence is None):
            raise ValueError(
                "un plato visible necesita state (o fill_ratio) y confidence"
            )
        return self


class TableReport(BaseModel):
    """Lectura de una mesa: salida de `Classifier.classify`."""

    items: list[Item] = Field(default_factory=list)
    people_present: bool = True  # si falta se asume True (03-contratos.md)
    notes: str | None = None

    @field_validator("items", mode="before")
    @classmethod
    def _drop_non_plates(cls, items: Any) -> Any:
        """Descarta vasos, botellas, etc. en vez de invalidar toda la lectura."""
        if not isinstance(items, list):
            return items
        kept = [
            i
            for i in items
            if not isinstance(i, dict) or i.get("kind", "plate") == "plate"
        ]
        if len(kept) < len(items):
            logger.info(
                "Se descartaron %d ítems que no son platos", len(items) - len(kept)
            )
        return kept
