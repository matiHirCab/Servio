"""Modelos de la observación de mesa (Jessica → Matías) y del aviso (Matías → Telegram).

Fuente de verdad: docs/context/03-contratos.md («Observación de mesa» y «Aviso»).
Si cambiás algo acá, cambiá también el doc y fixtures/.
"""

import re
from collections import Counter
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    model_validator,
)

from servio_vision.schemas import PlateState, state_from_fill_ratio

AlertType = Literal["mesa_por_terminar", "plato_vacio", "plato_lleno_mucho"]
AlertStatus = Literal["abierta", "resuelta"]


# Forma de fecha del contrato: ISO 8601 con "T", segundos y zona (Z o ±HH:MM). Hasta 6
# decimales: datetime no guarda más y los cortaría en silencio.
_ISO_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?(Z|[+-]\d{2}:\d{2})"
)


def _parse_datetime(value: Any) -> Any:
    # Acepta un datetime o un string con la forma del contrato (fixtures, log). Rechaza
    # timestamps unix, "2026-10-17 20:00:00Z" y formas compactas, que fromisoformat
    # aceptaría. Sin zona lo rechaza AwareDatetime.
    if isinstance(value, datetime):
        return value
    # fullmatch: con match y "$", "…Z\n" también pasaría.
    if isinstance(value, str) and _ISO_RE.fullmatch(value):
        return datetime.fromisoformat(value)
    raise ValueError("fecha ISO 8601 con T y zona, ej. 2026-10-17T20:00:00Z")


def _exact_int(value: Any) -> Any:
    # Literal[1] acepta True y 1.0 porque son == 1; acá solo vale un int de verdad.
    if type(value) is not int:
        raise ValueError("tiene que ser un entero")
    return value


def _none_as_true(value: Any) -> Any:
    # people_present: si falta o es null se asume True (03-contratos.md).
    return True if value is None else value


def _to_utc(value: datetime) -> datetime:
    # El contrato pide UTC: se acepta cualquier zona pero se guarda en UTC, para que el
    # log y los mensajes no mezclen zonas.
    return value.astimezone(UTC)


Timestamp = Annotated[
    AwareDatetime, BeforeValidator(_parse_datetime), AfterValidator(_to_utc)
]


class _Model(BaseModel):
    # Sin conversiones implícitas ("0.8" -> 0.8) y sin campos desconocidos (atrapa typos
    # como "fillRatio", que si no se perderían en silencio).
    model_config = ConfigDict(strict=True, extra="forbid")


class _Input(_Model):
    # La observación es de solo lectura. No usar validate_assignment con validadores de
    # modelo: pydantic asigna antes de correrlos y un error dejaría el valor malo puesto.
    model_config = ConfigDict(frozen=True)


class ObservedPlate(_Input):
    """Un plato visible y ya suavizado, dentro de una observación de mesa.

    `fill_ratio` y `confidence` son los de la última lectura que coincidió con `state`
    (así los devuelve smoothing.py), por eso se validan juntos y `confidence` nunca falta.
    """

    track_id: str = Field(min_length=1)  # {table_id}-{id del Item}
    state: PlateState
    fill_ratio: float | None = Field(default=None, ge=0, le=1)
    # Obligatorios: solo llegan platos visibles (schemas.Item los exige para esos), y
    # alerts/state.py empareja ids por posición, así que una vacía uniría platos distintos.
    confidence: float = Field(ge=0, le=1)
    position: str = Field(min_length=1)

    @model_validator(mode="after")
    def _state_matches_fill_ratio(self) -> "ObservedPlate":
        # schemas.Item ya calcula el estado desde fill_ratio: si acá no coinciden, es un bug.
        if self.fill_ratio is not None:
            expected = state_from_fill_ratio(self.fill_ratio)
            if expected != self.state:
                raise ValueError(
                    f"state {self.state!r} no coincide con fill_ratio {self.fill_ratio}"
                    f" ({expected!r})"
                )
        return self


class TableObservation(_Input):
    """Lo que el pipeline le pasa a `alerts.process()`: una mesa en una lectura."""

    schema_version: Annotated[Literal[1], BeforeValidator(_exact_int)]
    camera_id: str = Field(min_length=1)
    ts: Timestamp  # en un video, el tiempo del video
    table_id: str = Field(min_length=1)
    people_present: Annotated[bool, BeforeValidator(_none_as_true)] = True
    items: list[ObservedPlate]

    @model_validator(mode="after")
    def _valid_track_ids(self) -> "TableObservation":
        # El estado por plato (alerts/state.py) supone un track_id único y de esta mesa.
        prefix = f"{self.table_id}-"
        invalid = [
            i.track_id
            for i in self.items
            if not i.track_id.startswith(prefix) or len(i.track_id) == len(prefix)
        ]
        if invalid:
            raise ValueError(
                f"track_id tiene que ser {prefix}<id>: {', '.join(invalid)}"
            )
        repeated = [
            tid for tid, n in Counter(i.track_id for i in self.items).items() if n > 1
        ]
        if repeated:
            raise ValueError(f"track_id repetido: {', '.join(sorted(repeated))}")
        return self


class Alert(_Model):
    """Un aviso al grupo de mozos."""

    # El motor modifica el aviso abierto (reminders_sent += 1) y el tope se tiene que
    # seguir cumpliendo. Sin validadores de modelo, así un error no deja el valor puesto.
    model_config = ConfigDict(validate_assignment=True)

    id: str = Field(min_length=1)
    table_id: str = Field(min_length=1)
    type: AlertType
    message: str  # texto que se manda
    created_at: Timestamp
    status: AlertStatus
    # Máximo 2 recordatorios (05-reglas-alertas.md).
    reminders_sent: int = Field(default=0, ge=0, le=2)
    telegram_message_id: int | None = None
