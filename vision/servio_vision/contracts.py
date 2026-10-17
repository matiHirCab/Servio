"""Contrato entre módulos. Fuente de verdad: docs/context/03-contratos.md.

Modelos de lo que la visión manda al backend (`POST /api/observations`). Los de la
herramienta `report_table` del clasificador van aparte, en `schemas.py` (P1); `visible` vive
solo ahí y no viaja al backend.
Si cambiás algo acá, cambiá también el doc, web/lib/contracts.ts y fixtures/.
"""

import re
from datetime import datetime
from typing import Annotated, Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    model_validator,
)

from servio_vision.schemas import ItemKind, PlateState

AlertType = Literal[
    "mesa_por_terminar", "plato_vacio", "plato_lleno_mucho", "esperando_plato"
]
AlertStatus = Literal["abierta", "en_camino", "resuelta", "expirada"]

# Misma forma que acepta `z.string().datetime({ offset: true })` en web/.
_ISO_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$"
)


def _parse_iso(value: object) -> object:
    # Acepta datetime (se valida que tenga zona) o string ISO con "T" y zona. Rechaza
    # timestamps unix y "2026-10-17 20:00:00+00:00", que Zod no acepta.
    if isinstance(value, str):
        if not _ISO_RE.match(value):
            raise ValueError("fecha ISO 8601 con T y zona (Z o ±HH:MM)")
        return datetime.fromisoformat(value)
    return value


IsoDatetime = Annotated[AwareDatetime, BeforeValidator(_parse_iso)]


def _check_unique(ids: list[str], label: str) -> None:
    # La reconciliación y "ausente en 2 lecturas" suponen ids únicos.
    repeated = {i for i in ids if ids.count(i) > 1}
    if repeated:
        raise ValueError(f"{label} repetido: {', '.join(sorted(repeated))}")


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

    @model_validator(mode="after")
    def unique_track_ids(self) -> "TableReading":
        _check_unique([it.track_id for it in self.items], "track_id")
        return self


class Observation(_Strict):
    schema_version: Literal[1]
    camera_id: str = Field(min_length=1)
    ts: IsoDatetime
    tables: list[TableReading]

    @model_validator(mode="after")
    def unique_table_ids(self) -> "Observation":
        _check_unique([t.table_id for t in self.tables], "table_id")
        return self


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
