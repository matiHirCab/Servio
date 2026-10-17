"""Contrato entre módulos. Fuente de verdad: docs/context/03-contratos.md.

Modelos de lo que la visión manda al backend (`POST /api/observations`). Los de la
herramienta `report_table` del clasificador van aparte, en `schemas.py` (P1).
Si cambiás algo acá, cambiá también el doc, web/lib/contracts.ts y fixtures/.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

# Solo platos de comida: bebidas fuera de alcance.
ItemKind = Literal["plate"]
PlateState = Literal["lleno", "comiendo", "por_terminar", "vacio"]
AlertType = Literal["mesa_por_terminar", "plato_vacio", "plato_lleno_mucho"]
AlertStatus = Literal["abierta", "en_camino", "resuelta", "expirada"]


class ObservedItem(BaseModel):
    track_id: str = Field(min_length=1)
    kind: ItemKind
    state: PlateState
    confidence: float = Field(ge=0, le=1)
    fill_ratio: float | None = Field(default=None, ge=0, le=1)
    position: str | None = None


class TableReading(BaseModel):
    table_id: str = Field(min_length=1)
    # Si falta se asume True (ver 03-contratos.md).
    people_present: bool | None = None
    items: list[ObservedItem]


class Observation(BaseModel):
    schema_version: Literal[1]
    camera_id: str = Field(min_length=1)
    ts: datetime
    tables: list[TableReading]


class Alert(BaseModel):
    id: str = Field(min_length=1)
    table_id: str = Field(min_length=1)
    type: AlertType
    message: str
    created_at: datetime
    status: AlertStatus
    acked_by: str | None
    acked_at: datetime | None
    resolved_at: datetime | None
