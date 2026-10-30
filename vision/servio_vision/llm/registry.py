"""Los proveedores del modelo, por el nombre de `CLAUDE_PROVIDER`.

Sumar uno es un archivo nuevo en este paquete (con su `PROVIDER`, ver `base.py`) más
una línea acá."""

from servio_vision.llm import anthropic_api, claude_cli, openai_api
from servio_vision.llm.base import Provider

PROVIDERS: dict[str, Provider] = {
    p.name: p
    for p in (claude_cli.PROVIDER, anthropic_api.PROVIDER, openai_api.PROVIDER)
}
