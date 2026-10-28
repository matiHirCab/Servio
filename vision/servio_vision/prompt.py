"""Prompt y esquema de respuesta de Claude por plato (TOM-3).

Base en docs/context/04-vision.md («Prompt de Claude»). Iterar acá con el video.
"""

"""Prompts y esquema de respuesta por plato (TOM-3).

Los textos están en archivos para cambiarlos sin tocar código (`vision/prompts/`):
- `claude.txt`: instrucciones para Claude (`CLAUDE_PROVIDER=cli` o `anthropic`).
- `openai.json`: instrucciones, preguntas y niveles para la Decisions API de OpenAI
  (`CLAUDE_PROVIDER=openai`).

Para probar otro prompt sin pisar el default: `CLAUDE_PROMPT_FILE` / `OPENAI_PROMPT_FILE`
en el `.env` o el toml (`settings.py`), con la ruta a otro archivo (relativa a la raíz
del repo, o absoluta). Se leen en cada llamada: editar el archivo cambia la lectura
siguiente. Cada función acepta la ruta por parámetro; sin ella, la de la configuración.

Base en docs/context/04-vision.md («Prompt de Claude»).
"""

import json
from pathlib import Path

from servio_vision import settings

PROMPTS_DIR = settings.PROMPTS_DIR
DEFAULT_CLAUDE_PROMPT = PROMPTS_DIR / "claude.txt"
DEFAULT_OPENAI_PROMPT = PROMPTS_DIR / "openai.json"

# fill_ratio de cada nivel de la Decisions API (mismo orden que `levels` de
# openai.json, de menos a más), uno por estado de schemas.py: vacio, por_terminar,
# comiendo, comiendo, lleno. Va en código: a la API solo le llegan label y description.
FILL_RATIOS: list[float] = [0.0, 0.15, 0.45, 0.65, 1.0]
OPENAI_QUESTIONS = ("fill", "visible", "same_plate", "people_present")


class PromptError(ValueError):
    """El archivo de prompt no existe o no tiene la forma esperada."""


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as e:
        raise PromptError(f"No se pudo leer el prompt {path}: {e}") from e


def instructions(path: Path | None = None) -> str:
    """Instrucciones para Claude (`claude.txt` o `CLAUDE_PROMPT_FILE`)."""
    return _read(path or prompt_file("cli")).strip()


def system_prompt(path: Path | None = None) -> str:
    """cli y anthropic piden un JSON libre: las instrucciones más el pedido de JSON."""
    return (
        instructions(path)
        + "\nRespondé solo con el JSON pedido, un elemento por plato."
    )


def openai_prompt(path: Path | None = None) -> dict:
    """`openai.json` (o `OPENAI_PROMPT_FILE`), validado."""
    path = path or prompt_file("openai")
    try:
        cfg = json.loads(_read(path))
    except json.JSONDecodeError as e:
        raise PromptError(f"{path} no es un JSON válido: {e}") from e
    questions = cfg.get("questions", {})
    missing = [q for q in OPENAI_QUESTIONS if not questions.get(q)]
    if not cfg.get("instructions") or missing:
        raise PromptError(f"{path}: faltan instructions o las preguntas {missing}")
    levels = cfg.get("levels", [])
    if len(levels) != len(FILL_RATIOS) or not all(
        lv.get("label") and lv.get("description") for lv in levels
    ):
        raise PromptError(
            f"{path}: levels tiene que tener {len(FILL_RATIOS)} niveles con label y "
            "description, de menos a más comida"
        )
    return cfg


def prompt_file(provider: str) -> Path:
    """Archivo de prompt que usa ese proveedor según la configuración."""
    return settings.current().prompt_file_for(provider)


def check(provider: str, path: Path | None = None) -> None:
    """Lee y valida el prompt de ese proveedor; `PromptError` si está mal."""
    path = path or prompt_file(provider)
    if provider == "openai":
        openai_prompt(path)
    elif not instructions(path):
        raise PromptError(f"{path} está vacío")


def openai_instructions() -> str:
    text = openai_prompt()["instructions"]
    return "\n".join(text) if isinstance(text, list) else str(text)


def fill_levels() -> list[dict]:
    """Niveles de comida para la pregunta `fill` (sin campos de más)."""
    return [
        {"label": lv["label"], "description": lv["description"]}
        for lv in openai_prompt()["levels"]
    ]


# Esquema JSON de la respuesta (report_plates). Lo usan cli y anthropic.
REPORT_PLATES_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "plates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "fill_ratio": {"type": "number"},
                    "confidence": {"type": "number"},
                    "visible": {"type": "boolean"},
                    "same_plate": {"type": "boolean"},
                },
                "required": [
                    "index",
                    "fill_ratio",
                    "confidence",
                    "visible",
                    "same_plate",
                ],
                "additionalProperties": False,
            },
        },
        "people_present": {"type": "boolean"},
    },
    "required": ["plates", "people_present"],
    "additionalProperties": False,
}


def decision_questions(has_reference: list[bool]) -> list[dict]:
    """Preguntas para la Decisions API: por plato fill, visible y (con referencia)
    same_plate; más people_present para la mesa. Textos de `openai.json`."""
    cfg = openai_prompt()
    texts = cfg["questions"]
    levels = fill_levels()
    questions: list[dict] = []
    for i, ref in enumerate(has_reference, start=1):
        questions.append(
            {
                "type": "score",
                "name": f"fill_{i}",
                "instructions": texts["fill"].format(i=i),
                "levels": levels,
            }
        )
        questions.append(
            {
                "type": "predicate",
                "name": f"visible_{i}",
                "instructions": texts["visible"].format(i=i),
            }
        )
        if ref:
            questions.append(
                {
                    "type": "predicate",
                    "name": f"same_plate_{i}",
                    "instructions": texts["same_plate"].format(i=i),
                }
            )
    questions.append(
        {
            "type": "predicate",
            "name": "people_present",
            "instructions": texts["people_present"],
        }
    )
    return questions


def table_caption(table_id: str) -> str:
    return f"Mesa {table_id}, vista desde arriba:"


def plate_caption(index: int) -> str:
    return f"Plato {index}:"


def reference_caption(index: int) -> str:
    return f"Plato {index}, referencia (recién servido):"


def current_caption(index: int) -> str:
    return f"Plato {index}, ahora:"


def closing_text(n_plates: int) -> str:
    return (
        f"Hay {n_plates} platos. Devolvé un elemento por cada uno, con index de 1 a {n_plates}. "
        "Respondé solo con el JSON, sin texto antes ni después, con esta forma: "
        '{"plates": [{"index": 1, "fill_ratio": 0.5, "confidence": 0.8, "visible": true, "same_plate": true}], '
        '"people_present": true}'
    )
