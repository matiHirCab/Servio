"""Prompt y esquema de respuesta de Claude por plato (TOM-3).

Base en docs/context/04-vision.md («Prompt de Claude»). Iterar acá con el video.
"""

SYSTEM_PROMPT = """\
Sos el sistema de visión de un restaurante. La primera imagen es una vista desde arriba de una mesa.
Las siguientes son recortes de cada plato de esa mesa, numerados desde 1 en el mismo orden.
Para cada plato estimá:
- fill_ratio: qué fracción de la porción servida queda, de 0 (vacío o solo restos) a 1
  (recién servido, intacto). Es respecto de lo que se sirvió, no del tamaño del plato: un plato
  recién servido con la comida en el centro y el borde libre vale 1.
  Si no hay señales de que se comió (huecos en la porción, comida corrida o desparramada,
  salsa embarrada, restos sueltos), el plato está intacto: 0.9 a 1.
  Guía: 0.9–1 intacto · 0.5–0.8 se comió una parte · 0.1–0.3 quedan restos · 0–0.05 vacío.
- confidence: de 0 a 1.
- visible: false solo si no se ve la comida del plato (una persona o un brazo tapa más de la mitad).
  Cubiertos, servilletas o una mano en el borde no cuentan: estimá igual con lo que se ve.
  Un tenedor apoyado, una mano o un brazo encima no significan que se comió: no bajan el
  fill_ratio; estimá la comida que se ve alrededor.
Indicá también si hay personas sentadas en la mesa (people_present).
Respondé solo con el JSON pedido, un elemento por plato."""

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
                },
                "required": ["index", "fill_ratio", "confidence", "visible"],
                "additionalProperties": False,
            },
        },
        "people_present": {"type": "boolean"},
    },
    "required": ["plates", "people_present"],
    "additionalProperties": False,
}


def table_caption(table_id: str) -> str:
    return f"Mesa {table_id}, vista desde arriba:"


def plate_caption(index: int) -> str:
    return f"Plato {index}:"


def closing_text(n_plates: int) -> str:
    return (
        f"Hay {n_plates} platos. Devolvé un elemento por cada uno, con index de 1 a {n_plates}. "
        "Respondé solo con el JSON, sin texto antes ni después, con esta forma: "
        '{"plates": [{"index": 1, "fill_ratio": 0.5, "confidence": 0.8, "visible": true}], '
        '"people_present": true}'
    )
