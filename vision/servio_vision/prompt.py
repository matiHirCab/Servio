"""Prompt y esquema de respuesta de Claude por plato (TOM-3).

Base en docs/context/04-vision.md («Prompt de Claude»). Iterar acá con el video.
"""

SYSTEM_PROMPT = """\
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
- visible: false si una mano, un brazo o un cubierto agarrado está encima de la comida o
  agarrando comida del plato, o si algo tapa la comida: así no se puede medir bien. Una mano
  o un cubierto apoyados en el borde, sin tapar comida, no cuentan: visible true.
- same_plate: si el plato trae referencia, false cuando la referencia muestra otra comida
  que la de ahora (entonces ignorá la referencia y estimá sin ella). Sin referencia, true.
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
