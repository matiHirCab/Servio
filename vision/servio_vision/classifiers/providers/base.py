"""Lo que comparten los proveedores: errores, el pedido y el formato de bloques.

Cada proveedor (`cli.py`, `anthropic_api.py`, `openai_api.py`) expone
`call(request, model, timeout_s) -> (datos, uso)` y lanza `ClaudeCallError` si falla.
Sumar uno es un archivo nuevo más una línea en el registro de `claude_plates.py`.
"""

from collections.abc import Callable
from dataclasses import dataclass


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
class PlatesRequest:
    """Lo que se manda por una mesa: el contenido ya armado y, por plato, si lleva
    referencia (la Decisions API pregunta same_plate solo para esos)."""

    content: list
    has_reference: list[bool]


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
