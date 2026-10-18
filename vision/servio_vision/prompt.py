"""Prompt y esquema de respuesta de Claude por plato (TOM-3).

Base en docs/context/04-vision.md («Prompt de Claude»). Iterar acá con el video.
"""

INSTRUCTIONS = """\
Sos el sistema de visión de un restaurante. La primera imagen es una vista desde arriba de una mesa.
Las siguientes son recortes de cada plato de esa mesa, numerados desde 1 en el mismo orden.
Algunos platos traen antes una referencia: ese mismo plato recién servido, intacto.
Para cada plato estimá:
- fill_ratio: qué fracción de la porción servida queda, de 0 (vacío o solo restos) a 1
  (recién servido, intacto).
  Si el plato trae referencia, la referencia vale 1: compará cuánta comida hay ahora contra
  ella y contá las piezas (si había 3 galletitas y queda 1 entera, es 0.33 aunque esa esté
  intacta). La referencia manda sobre cómo se ve la comida que queda.
  Sin referencia: es respecto de lo que se sirvió, no del tamaño del plato; un plato recién
  servido con la comida en el centro y el borde libre vale 1. Si no hay señales de que se
  comió (huecos en la porción, comida corrida o desparramada, salsa embarrada, restos
  sueltos), está intacto: 0.9 a 1.
  Guía: 0.9–1 intacto · 0.5–0.8 se comió una parte · 0.1–0.3 quedan restos · 0–0.05 vacío.
- confidence: de 0 a 1.
- visible: false solo si una mano, un brazo o un cubierto agarrado está encima de la comida o
  agarrando comida del plato: así no se puede medir bien. Cubiertos apoyados (también cruzados
  encima del plato), una servilleta o una mano en el borde no cuentan: visible true, estimá con
  lo que se ve. Un plato con los cubiertos apoyados y sin comida a la vista está vacío (0).
- same_plate: si el plato trae referencia, false cuando la referencia muestra otra comida
  que la de ahora (entonces ignorá la referencia y estimá sin ella). Sin referencia, true.
Indicá también si hay personas sentadas en la mesa (people_present)."""

# cli y anthropic piden un JSON libre; la Decisions API (openai) usa INSTRUCTIONS solas
# porque responde preguntas tipadas.
SYSTEM_PROMPT = (
    INSTRUCTIONS + "\nRespondé solo con el JSON pedido, un elemento por plato."
)

# Esquema JSON de la respuesta (report_plates). Lo usan los dos proveedores.
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


# Decisions API de OpenAI (CLAUDE_PROVIDER=openai): en vez de un JSON libre, una
# pregunta tipada por dato. Los niveles de fill van de menos a más (la API los numera
# desde 0) y se describen con las palabras de la guía de INSTRUCTIONS, sin números: los
# rangos de la guía no coinciden con los umbrales de estado de schemas.py.
FILL_LEVELS: list[dict] = [
    {"label": "vacío", "description": "Vacío o solo restos mínimos."},
    {"label": "restos", "description": "Quedan restos: se comió casi todo."},
    {"label": "mitad", "description": "Se comió bastante: queda cerca de la mitad."},
    {"label": "parte", "description": "Se comió una parte, pero queda la mayoría."},
    {
        "label": "intacto",
        "description": "Recién servido: sin señales de que se comió, o igual a la referencia.",
    },
]
# fill_ratio de cada nivel (mismo orden), uno por estado de schemas.py: vacio,
# por_terminar, comiendo, comiendo, lleno. Va aparte porque a la API solo le llegan
# label y description.
FILL_RATIOS: list[float] = [0.0, 0.15, 0.45, 0.65, 1.0]


def decision_questions(has_reference: list[bool]) -> list[dict]:
    """Preguntas para la Decisions API: por plato fill, visible y (con referencia)
    same_plate; más people_present para la mesa."""
    questions: list[dict] = []
    for i, ref in enumerate(has_reference, start=1):
        questions.append(
            {
                "type": "score",
                "name": f"fill_{i}",
                "instructions": (
                    f"Plato {i}: ¿qué fracción de la porción servida queda? "
                    "Elegí el nivel que mejor describe cuánto queda, con los criterios de "
                    "fill_ratio de las instrucciones (referencia, contar piezas, señales "
                    "de que se comió). Si la referencia muestra otra comida que la de "
                    "ahora, ignorala y estimá sin ella."
                ),
                "levels": FILL_LEVELS,
            }
        )
        questions.append(
            {
                "type": "predicate",
                "name": f"visible_{i}",
                "instructions": (
                    f"Plato {i}: ¿se puede medir la comida? Falso solo si una mano, un brazo "
                    "o un cubierto agarrado está encima de la comida o agarrando comida del "
                    "plato. Cubiertos apoyados, una servilleta o una mano en el borde: verdadero."
                ),
            }
        )
        if ref:
            questions.append(
                {
                    "type": "predicate",
                    "name": f"same_plate_{i}",
                    "instructions": (
                        f"Plato {i}: ¿la referencia (recién servido) muestra la misma "
                        "comida que el recorte de ahora?"
                    ),
                }
            )
    questions.append(
        {
            "type": "predicate",
            "name": "people_present",
            "instructions": "¿Hay personas sentadas en la mesa?",
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
