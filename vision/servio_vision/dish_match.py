"""Qué plato del pedido es cada plato que aparece en cámara.

El sistema del restaurante sabe qué pidió cada mesa (la comanda) pero no cuál de los
platos servidos es cuál. Cuando aparece un plato nuevo, se le muestra el recorte a
Claude con las opciones que quedan del pedido de esa mesa y elige una. Es una llamada
chica por plato, una sola vez. Si queda una sola opción no hace falta preguntar, y si
Claude no está disponible (sin API key o falla), se usa el orden del pedido.
"""

import base64
import json
import logging
import os

from servio_vision.restaurant import Dish

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-haiku-5-5"
PROMPT = (
    "La imagen es un plato servido en un restaurante, visto desde arriba. "
    "¿Cuál de estos platos del pedido de la mesa es? Opciones:\n{options}\n"
    "Respondé con el número de la opción."
)


class DishMatcher:
    """Elige, mirando el recorte del plato, cuál de las opciones del pedido es."""

    def __init__(self, model: str | None = None) -> None:
        self.model = model or os.getenv("CLAUDE_MODEL") or DEFAULT_MODEL
        self._client = None
        if os.getenv("ANTHROPIC_API_KEY"):
            import anthropic

            self._client = anthropic.Anthropic(timeout=10.0, max_retries=1)
        else:
            logger.info(
                "Sin ANTHROPIC_API_KEY: los platos se asignan por orden de pedido"
            )

    def match(self, jpeg: bytes, options: list[Dish]) -> Dish | None:
        if not options:
            return None
        if len(options) == 1 or self._client is None:
            return options[0]
        listing = "\n".join(f"{i}. {d.name}" for i, d in enumerate(options))
        schema = {
            "type": "object",
            "properties": {
                "opcion": {"type": "integer", "enum": list(range(len(options)))}
            },
            "required": ["opcion"],
            "additionalProperties": False,
        }
        try:
            response = self._client.messages.create(
                model=self.model,
                max_tokens=1024,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": "image/jpeg",
                                    "data": base64.standard_b64encode(jpeg).decode(),
                                },
                            },
                            {"type": "text", "text": PROMPT.format(options=listing)},
                        ],
                    }
                ],
                output_config={
                    "format": {"type": "json_schema", "schema": schema},
                    "effort": "low",
                },
            )
            text = next(b.text for b in response.content if b.type == "text")
            choice = options[json.loads(text)["opcion"]]
            logger.info("Plato identificado: %s", choice.name)
            return choice
        except Exception as error:  # noqa: BLE001 — si falla, el orden del pedido alcanza
            logger.warning("No se pudo identificar el plato (%s): uso el pedido", error)
            return options[0]
