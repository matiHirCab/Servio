"""Lo que comparten el paquete `llm` y sus proveedores: la interfaz `PlateEstimator`,
los errores, el pedido y el formato de bloques.

Cada proveedor (`claude_cli.py`, `anthropic_api.py`, `openai_api.py`) expone un
`PROVIDER = Provider(nombre, formato, call)`, con `call(request, model, timeout_s) ->
(datos, uso)` que lanza `ClaudeCallError` si falla. Sumar uno es un archivo nuevo más
una línea en `registry.py`.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from servio_vision.llm.parsing import PlatesReport


@dataclass(frozen=True)
class EstimateRequest:
    """Lo que se le pregunta a un `PlateEstimator` por una mesa: los recortes de cada
    plato (JPEG) y, por plato, su recorte recién servido o `None`."""

    plate_jpegs: list[bytes]
    table_id: str
    references: list[bytes | None] | None = None


class PlateEstimator(Protocol):
    """Estima cuánta comida queda en cada plato de una mesa. Quien la usa (`hybrid`)
    no sabe qué modelo hay detrás. `None` si no pudo (el `hybrid` usa entonces YOLO)."""

    def estimate(self, request: EstimateRequest) -> PlatesReport | None: ...


class UnknownProviderError(ValueError):
    """`CLAUDE_PROVIDER` no es uno de los del registro (`registry.PROVIDERS`)."""


class ClaudeCallError(RuntimeError):
    """La llamada a Claude falló o devolvió algo que no se pudo leer."""


class PermanentCallError(ClaudeCallError):
    """Falló y reintentar no lo arregla: una clave inválida, un paquete o un `claude`
    que faltan, un modelo que no existe. No se reintenta y abre el cortacircuito."""


# Estados HTTP que no se arreglan esperando: clave inválida, sin permiso, modelo que
# no existe.
PERMANENT_STATUS = {401, 403, 404}


def api_error(prefix: str, error: Exception) -> ClaudeCallError:
    status = getattr(error, "status_code", None)
    kind = PermanentCallError if status in PERMANENT_STATUS else ClaudeCallError
    return kind(f"{prefix}: {error}")


@dataclass(frozen=True)
class ProviderRequest:
    """Lo que se manda por una mesa: el contenido ya armado y, por plato, si lleva
    referencia (la Decisions API pregunta same_plate solo para esos).

    `system` y `schema`: otro prompt y otro esquema de respuesta. Sin ellos van los de
    los recortes de plato; el clasificador `claude` (la mesa entera) pasa los suyos."""

    content: list
    has_reference: list[bool]
    system: str | None = None
    schema: dict | None = None


@dataclass(frozen=True)
class Format:
    """Cómo arma los bloques cada API."""

    text: Callable[[str], dict]
    image: Callable[[str], dict]  # recibe la imagen en base64
    closing: bool  # agrega el cierre que pide el JSON


ANTHROPIC_FORMAT = Format(
    text=lambda text: {"type": "text", "text": text},
    image=lambda data: {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/jpeg", "data": data},
    },
    closing=True,
)
# La Decisions API responde preguntas tipadas: sin el cierre que pide JSON.
OPENAI_FORMAT = Format(
    text=lambda text: {"type": "input_text", "text": text},
    image=lambda data: {
        "type": "input_image",
        "image_url": f"data:image/jpeg;base64,{data}",
    },
    closing=False,
)


@dataclass(frozen=True)
class Provider:
    """Un proveedor del modelo: cómo arma los bloques y cómo hace la llamada."""

    name: str  # el valor de CLAUDE_PROVIDER
    format: Format
    call: Callable[[ProviderRequest, str, float], tuple[dict, dict]]
