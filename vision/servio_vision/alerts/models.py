"""Modelos de la observación de mesa (Jessica → Matías) y del aviso (Matías → Telegram).

Fuente de verdad: docs/context/03-contratos.md («Observación de mesa» y «Aviso»).
Si cambiás algo acá, cambiá también el doc y fixtures/.
"""

from collections import Counter
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import (
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


def _parse_datetime(value: Any) -> Any:
    # Fechas desde JSON (fixtures, log) o datetime; sin zona las rechaza AwareDatetime.
    if isinstance(value, str):
        return datetime.fromisoformat(value)
    return value


def _exact_int(value: Any) -> Any:
    # Literal[1] acepta True y 1.0 porque son == 1; acá solo vale un int de verdad.
    if type(value) is not int:
        raise ValueError("tiene que ser un entero")
    return value


def _none_as_true(value: Any) -> Any:
    # people_present: si falta o es null se asume True (03-contratos.md).
    return True if value is None else value


Timestamp = Annotated[AwareDatetime, BeforeValidator(_parse_datetime)]


class _Model(BaseModel):
    # Sin conversiones implícitas ("0.8" -> 0.8) y sin campos desconocidos (atrapa typos
    # como "fillRatio", que si no se perderían en silencio).
    model_config = ConfigDict(strict=True, extra="forbid")


class ObservedPlate(_Model):
    """Un plato visible y ya suavizado, dentro de una observación de mesa."""

    track_id: str = Field(min_length=1)  # {table_id}-{id del Item}
    state: PlateState
    fill_ratio: float | None = Field(default=None, ge=0, le=1)
    confidence: float | None = Field(default=None, ge=0, le=1)
    position: str | None = None

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


class TableObservation(_Model):
    """Lo que el pipeline le pasa a `alerts.process()`: una mesa en una lectura."""

    schema_version: Annotated[Literal[1], BeforeValidator(_exact_int)]
    camera_id: str = Field(min_length=1)
    ts: Timestamp  # en un video, el tiempo del video
    table_id: str = Field(min_length=1)
    people_present: Annotated[bool, BeforeValidator(_none_as_true)] = True
    items: list[ObservedPlate]

    @model_validator(mode="after")
    def _unique_track_ids(self) -> "TableObservation":
        # El estado por plato (alerts/state.py) supone un track_id único por mesa.
        repeated = [
            tid for tid, n in Counter(i.track_id for i in self.items).items() if n > 1
        ]
        if repeated:
            raise ValueError(f"track_id repetido: {', '.join(sorted(repeated))}")
        return self


class Alert(_Model):
    """Un aviso al grupo de mozos."""

    id: str = Field(min_length=1)
    table_id: str = Field(min_length=1)
    type: AlertType
    message: str  # texto que se manda
    created_at: Timestamp
    status: AlertStatus
    reminders_sent: int = Field(default=0, ge=0)
    telegram_message_id: int | None = None
