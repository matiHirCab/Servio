"""Proveedor `anthropic`: la API de Anthropic con `ANTHROPIC_API_KEY`."""

import json

from servio_vision import prompt, settings
from servio_vision.classifiers.providers.base import (
    ClaudeCallError,
    PermanentCallError,
    PlatesRequest,
    api_error,
)


def call(request: PlatesRequest, model: str, timeout_s: float) -> tuple[dict, dict]:
    """API de Anthropic con salida estructurada (`output_config.format`)."""
    try:
        import anthropic
    except ImportError as e:
        raise PermanentCallError(
            "falta el paquete anthropic: pip install anthropic"
        ) from e

    # la key de la configuración (settings.py); sin ella, la que el SDK encuentre
    client = anthropic.Anthropic(
        api_key=settings.current().anthropic_api_key or None,
        timeout=timeout_s,
        max_retries=0,
    )
    try:
        resp = client.messages.create(
            model=model,
            max_tokens=2048,
            system=prompt.system_prompt(),
            messages=[{"role": "user", "content": request.content}],
            output_config={
                "format": {
                    "type": "json_schema",
                    "schema": prompt.REPORT_PLATES_SCHEMA,
                },
                "effort": "low",
            },
        )
    except anthropic.APIError as e:
        raise api_error("API de Anthropic", e) from e
    if resp.stop_reason == "refusal":
        raise ClaudeCallError("Claude rechazó el pedido")
    text = next((b.text for b in resp.content if b.type == "text"), "")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ClaudeCallError("la API no devolvió JSON") from e
    return data, {
        "input_tokens": resp.usage.input_tokens,
        "output_tokens": resp.usage.output_tokens,
    }
