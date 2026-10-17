"""Contrato entre módulos. Fuente de verdad: docs/context/03-contratos.md.

Modelos de lo que la visión manda al backend (`POST /api/observations`). Los de la
herramienta `report_table` del clasificador van aparte, en `schemas.py` (P1).
Si cambiás algo acá, cambiá también el doc, web/lib/contracts.ts y fixtures/.
"""

from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

# Solo platos de comida: bebidas fuera de alcance.
ItemKind = Literal["plate"]
PlateState = Literal["lleno", "comiendo", "por_terminar", "vacio"]
AlertType = Literal["mesa_por_terminar", "plato_vacio", "plato_lleno_mucho"]
AlertStatus = Literal["abierta", "en_camino", "resuelta", "expirada"]

# Fecha ISO 8601 con zona, igual que Zod en web/: sin zona se rechaza. No estricta para
# aceptar strings ISO también desde dicts.
IsoDatetime = Annotated[AwareDatetime, Field(strict=False)]


class _Strict(BaseModel):
    # Sin conversiones implícitas (por ejemplo "0.8" -> 0.8), como Zod.
    model_config = ConfigDict(strict=True)


class ObservedItem(_Strict):
    track_id: str = Field(min_length=1)
    kind: ItemKind
    state: PlateState
    confidence: float = Field(ge=0, le=1)
    fill_ratio: float | None = Field(default=None, ge=0, le=1)
    position: str | None = None


class TableReading(_Strict):
    table_id: str = Field(min_length=1)
    # Si falta (o es None) se asume True (ver 03-contratos.md).
    people_present: bool | None = None
    items: list[ObservedItem]


class Observation(_Strict):
    schema_version: Literal[1]
    camera_id: str = Field(min_length=1)
    ts: IsoDatetime
    tables: list[TableReading]


class Alert(_Strict):
    id: str = Field(min_length=1)
    table_id: str = Field(min_length=1)
    type: AlertType
    message: str
    created_at: IsoDatetime
    status: AlertStatus
    acked_by: str | None
    acked_at: IsoDatetime | None
    resolved_at: IsoDatetime | None
