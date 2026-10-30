"""El modelo que lee los platos (Claude por defecto, u OpenAI; `CLAUDE_PROVIDER`).

Quien lo usa (`hybrid`) recibe un `PlateEstimator` y no sabe cuál es. Dos caminos, con
distinta privacidad (C-3, docs/legal-uruguay.md):

- `estimate` (el normal, `hybrid`): recibe los recortes de cada plato (JPEG) y devuelve,
  por plato, `fill_ratio`, `confidence`, `visible` y `same_plate`, más `people_present`.
  **Al modelo van solo recortes de plato, nunca la mesa.**
- `estimate_table` (solo el clasificador `claude`): **manda la imagen de la mesa
  entera**; es opt-in (`CLAUDE_ALLOW_TABLE_IMAGES`) y quien lo llama tiene que haber
  pixelado a las personas antes; ver `classifiers/claude.py`.

Proveedor según `CLAUDE_PROVIDER`:
- `cli` (por defecto): el Claude Code instalado y logueado (`claude -p`), con la
  suscripción del equipo. No necesita API key.
- `anthropic`: la API de Anthropic con `ANTHROPIC_API_KEY` (respaldo; `pip install anthropic`).
- `openai`: la Decisions API de OpenAI con `OPENAI_API_KEY` (`pip install "openai>=3.26.0"`).
  En vez de un JSON libre hace preguntas tipadas por plato (ver `prompt.decision_questions`).

Variables opcionales: `CLAUDE_MODEL` (por defecto `haiku` / `claude-haiku-5-5`), `CLAUDE_TIMEOUT_S`
(por defecto 15 s con `cli`, 10 s con `anthropic`), `CLAUDE_CLI` (ruta a `claude.exe` si no está en
el PATH). Con `openai`: `OPENAI_MODEL` (por defecto `gpt-6-luna`) y `OPENAI_TIMEOUT_S` (por defecto
14 s); van aparte para que un valor pensado para Claude no termine en OpenAI.

Qué hay en cada archivo:
- `base.py`: la interfaz `PlateEstimator`, el pedido, los errores y el formato de bloques.
- `parsing.py`: los reportes y su validación, comunes a todos.
- `claude_cli.py`, `anthropic_api.py`, `openai_api.py`: un proveedor por archivo.
- `registry.py`: los proveedores por nombre. Sumar uno es un archivo nuevo más una línea acá.
- `runtime.py`: lo común de llamar (cortacircuito, tope por día, reintento) y `make_estimator`.

Prueba rápida: `python -m servio_vision.llm plato1.jpg plato2.jpg`
"""

from servio_vision.llm.base import EstimateRequest, PlateEstimator
from servio_vision.llm.parsing import (
    PlateEstimate,
    PlatesReport,
    TableEstimate,
    TablePlate,
)
from servio_vision.llm.runtime import ProviderEstimator, make_estimator, reset_state

__all__ = [
    "EstimateRequest",
    "PlateEstimate",
    "PlateEstimator",
    "PlatesReport",
    "ProviderEstimator",
    "TableEstimate",
    "TablePlate",
    "make_estimator",
    "reset_state",
]
