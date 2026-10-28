"""Roles y permisos: qué ve el mozo, el encargado y el dueño (R-1).

La asignación rol → vistas, mesas, avisos y métricas se lee de la sección
`[roles]` de `config/servio.toml`, no está fija en el código: cambiar qué ve un
rol es editar ese archivo. Acá solo vive el catálogo de métricas que existen
(cada una necesita código para calcularse) y la validación.

El dashboard (D-1) y `tools/metricas.py --rol` preguntan con `Roles.can_see()`.
Tabla y criterio en docs/context/12-roles.md.
"""

import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from servio_vision import VISION_DIR

DEFAULT_CONFIG = VISION_DIR / "config" / "servio.toml"

# id → nombre para mostrar. Son las métricas de M-1; las que todavía no se calculan
# igual están, para que la config del local no cambie cuando se agreguen.
METRICS = {
    "avisos_abiertos": "Avisos abiertos y quién los tomó",
    "tiempo_respuesta": "Tiempo de respuesta por mozo (turno y día)",
    "mesa_terminada_sin_atender": "Mesa terminada sin atender",
    "mesas_olvidadas": "Mesas olvidadas (lleno mucho rato)",
    "rotacion": "Tiempo de mesa y rotación del día",
    "resumen_cierre": "Resumen al cerrar el servicio",
    "platos_que_vuelven": "Platos que vuelven con comida (% y motivo)",
    "desperdicio": "Desperdicio en $",
    "historico": "Histórico y tendencias (más de 7 días)",
    "antes_despues": "Antes vs. después con Servio",
    "costos_y_configuracion": "Costo de Servio, configuración, usuarios y roles",
}
VIEWS = ("reconocimiento", "dashboard", "configuracion")
View = Literal["reconocimiento", "dashboard", "configuracion"]


class Role(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nombre: str
    admin: bool = False
    vistas: list[View] = Field(default_factory=list)
    mesas: Literal["todas", "suyas"] = "todas"
    avisos: Literal["suyas", "todas", "ninguno"] = "ninguno"
    metricas: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _known_metrics(self) -> "Role":
        unknown = [m for m in self.metricas if m not in METRICS]
        if unknown:
            raise ValueError(
                f"métricas desconocidas en el rol {self.nombre}: {', '.join(unknown)}"
                f" (las que hay: {', '.join(METRICS)})"
            )
        return self

    def visible_metrics(self) -> list[str]:
        """Ids de las métricas que ve, en el orden del catálogo."""
        allowed = set(METRICS) if self.admin else set(self.metricas)
        return [m for m in METRICS if m in allowed]

    def visible_views(self) -> list[str]:
        return list(VIEWS) if self.admin else [v for v in VIEWS if v in self.vistas]


class Roles:
    def __init__(self, roles: dict[str, Role]):
        self.roles = roles

    @classmethod
    def load(cls, path: Path = DEFAULT_CONFIG) -> "Roles":
        data = tomllib.loads(Path(path).read_text(encoding="utf-8"))
        raw = data.get("roles")
        if not raw:
            raise ValueError(f"{path} no tiene la sección [roles]")
        return cls({role_id: Role(**values) for role_id, values in raw.items()})

    def get(self, role_id: str) -> Role:
        try:
            return self.roles[role_id]
        except KeyError:
            raise KeyError(
                f"rol desconocido: {role_id} (los que hay: {', '.join(self.roles)})"
            ) from None

    def can_see(self, role_id: str, metric: str) -> bool:
        if metric not in METRICS:
            raise KeyError(f"métrica desconocida: {metric}")
        return metric in self.get(role_id).visible_metrics()

    def can_open(self, role_id: str, view: str) -> bool:
        return view in self.get(role_id).visible_views()
