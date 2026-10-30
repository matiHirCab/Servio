"""Los reportes del modelo y su validación, comunes a todos los proveedores.

Cada proveedor devuelve un dict; acá se valida contra estos esquemas (un error se
reintenta como uno del proveedor, ver `runtime._call_model`)."""

from pydantic import BaseModel, Field

from servio_vision.tracking import POSITION_LABELS


class PlateEstimate(BaseModel):
    index: int = Field(ge=1, description="Número del recorte, desde 1.")
    fill_ratio: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    visible: bool = True
    same_plate: bool = True  # False: la referencia es de otra comida (id cambiado)


class PlatesReport(BaseModel):
    plates: list[PlateEstimate]
    people_present: bool = True


class TablePlate(BaseModel):
    """Un plato de la mesa entera: el modelo lo ubica en la grilla 3×3."""

    position: str
    fill_ratio: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    visible: bool = True


class TableEstimate(BaseModel):
    plates: list[TablePlate]
    people_present: bool = True


def parse_plates(data: dict, n_plates: int) -> PlatesReport:
    report = PlatesReport.model_validate(data)
    indices = sorted(p.index for p in report.plates)
    if indices != list(range(1, n_plates + 1)):
        raise ValueError(f"esperaba platos 1..{n_plates}, llegaron {indices}")
    report.plates.sort(key=lambda p: p.index)
    return report


def parse_table(data: dict) -> TableEstimate:
    estimate = TableEstimate.model_validate(data)
    positions = [p.position for p in estimate.plates]
    unknown = sorted(set(positions) - set(POSITION_LABELS))
    if unknown:
        raise ValueError(f"posiciones que no existen: {unknown}")
    repeated = sorted({p for p in positions if positions.count(p) > 1})
    if repeated:  # dos platos en la misma celda: no se pueden seguir por posición
        raise ValueError(f"dos platos en la misma posición: {repeated}")
    return estimate
