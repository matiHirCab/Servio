"""Modelos de la herramienta `report_table` (contrato P1 ↔ P3).

Fuente de verdad: docs/context/03-contratos.md («Visión interna») y
docs/context/04-vision.md («Prompt»). Cambios acá van en un PR revisado por P3.
"""

from typing import Literal

from pydantic import BaseModel, Field

ItemKind = Literal["plate"]  # solo platos de comida: bebidas fuera de alcance
PlateState = Literal["lleno", "comiendo", "por_terminar", "vacio"]

# Umbrales de fill_ratio (fracción de comida que queda) entre estados.
FULL_MIN = 0.80  # > 0.80 → lleno
EATING_MIN = 0.30  # 0.30–0.80 → comiendo (no dispara avisos)
EMPTY_MAX = 0.05  # 0.05–0.30 → por_terminar · < 0.05 → vacio


def state_from_fill_ratio(fill_ratio: float) -> PlateState:
    """Estado de un plato según cuánta comida le queda (0 a 1)."""
    if fill_ratio > FULL_MIN:
        return "lleno"
    if fill_ratio >= EATING_MIN:
        return "comiendo"
    if fill_ratio >= EMPTY_MAX:
        return "por_terminar"
    return "vacio"


class Item(BaseModel):
    """Un plato sobre la mesa, tal como lo devuelve el clasificador."""

    id: str = Field(min_length=1, description='Id estable entre lecturas, ej. "p1".')
    kind: ItemKind = "plate"
    state: PlateState
    fill_ratio: float | None = Field(
        default=None, ge=0, le=1, description="Fracción de comida que queda."
    )
    position: str = Field(description='Texto libre, ej. "arriba-izquierda".')
    confidence: float = Field(ge=0, le=1)
    visible: bool = Field(
        default=True,
        description="False si algo lo tapa: no cuenta como lectura en el suavizado.",
    )


class TableReport(BaseModel):
    """Lectura de una mesa: salida de `Classifier.classify`."""

    items: list[Item] = Field(default_factory=list)
    people_present: bool = True  # si falta se asume True (03-contratos.md)
    notes: str | None = None
