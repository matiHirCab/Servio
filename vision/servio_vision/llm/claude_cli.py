"""Proveedor `cli`: el Claude Code instalado y logueado (`claude -p`), con la
suscripción del equipo. No necesita API key."""

import json
import os
import shutil
import subprocess
import tempfile
from collections.abc import Mapping

from servio_vision import prompt, settings
from servio_vision.llm.base import (
    ANTHROPIC_FORMAT,
    ClaudeCallError,
    PermanentCallError,
    Provider,
    ProviderRequest,
)


def _find_cli(cli: str | None = None) -> str:
    """`cli` (CLAUDE_CLI de la configuración) o el `claude` del PATH."""
    # En Windows hay que llamar a claude.exe: claude.cmd rompe los argumentos.
    exe = (
        cli
        or settings.current().claude_cli
        or shutil.which("claude.exe")
        or shutil.which("claude")
    )
    if not exe:
        raise PermanentCallError(
            "No encuentro el comando claude (instalá Claude Code o usá CLAUDE_CLI)"
        )
    return exe


def json_from_text(text: str) -> dict:
    """Saca el objeto JSON de una respuesta de texto (puede venir entre ``` o con texto)."""
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            pass
    raise ClaudeCallError(f"claude -p no devolvió JSON: {text[:300]!r}")


# Variables que no le llegan a `claude -p`: las claves y los tokens de Servio
# (settings.SECRET_KEYS) y el token de login alternativo de Anthropic.
_CLI_HIDDEN = {k.upper() for k in settings.SECRET_KEYS} | {"ANTHROPIC_AUTH_TOKEN"}


def _cli_env(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """El entorno de `claude -p`: el nuestro sin los secretos.

    Con una `ANTHROPIC_API_KEY` el CLI deja la suscripción del equipo y factura por
    API sin avisar; y el token de Telegram o la URL de la base no tienen por qué
    llegarle a un proceso hijo. El resto (PATH, el perfil donde guarda su login) sí."""
    env = {
        k: v for k, v in (environ or os.environ).items() if k.upper() not in _CLI_HIDDEN
    }
    # sin esto, claude se actualiza solo en medio de la corrida y el siguiente
    # llamado no encuentra el ejecutable
    env["DISABLE_AUTOUPDATER"] = "1"
    return env


def call(request: ProviderRequest, model: str, timeout_s: float) -> tuple[dict, dict]:
    """`claude -p` con las imágenes por stdin (stream-json).

    El prompt va dentro del mensaje y no como argumento: en Windows, si `claude` es
    un `.cmd`, los argumentos de varias líneas o con comillas llegan rotos.
    """
    exe = _find_cli()
    instructions = ANTHROPIC_FORMAT.text(request.system or prompt.system_prompt())
    message = {
        "type": "user",
        "message": {"role": "user", "content": [instructions, *request.content]},
    }
    args = [
        exe,
        "-p",
        "--input-format",
        "stream-json",
        "--output-format",
        "stream-json",
        "--verbose",
        "--model",
        model,
        "--effort",
        "low",
        "--tools",
        "",
        "--no-session-persistence",
    ]
    if not exe.lower().endswith((".cmd", ".bat")):
        # salida validada contra el esquema (structured_output)
        schema = request.schema or prompt.REPORT_PLATES_SCHEMA
        args += ["--json-schema", json.dumps(schema)]
    try:
        out = subprocess.run(
            args,
            input=json.dumps(message) + "\n",
            capture_output=True,
            check=False,
            text=True,
            encoding="utf-8",
            timeout=timeout_s,
            cwd=tempfile.gettempdir(),  # que no cargue el CLAUDE.md del repo
            env=_cli_env(),
        )
    except subprocess.TimeoutExpired as e:
        raise ClaudeCallError(f"claude -p tardó más de {timeout_s} s") from e
    result = None
    for line in out.stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "result":
            result = event
    data = (result or {}).get("structured_output")
    if data is None and result is not None:
        # sin --json-schema o versión vieja: el JSON viene como texto. Si está,
        # sirve aunque claude haya salido con error después de responder.
        try:
            data = json_from_text(result.get("result") or "")
        except ClaudeCallError:
            data = None
    if data is None:
        detail = (result or {}).get("result") or out.stderr or out.stdout
        raise ClaudeCallError(
            f"claude -p falló (código {out.returncode}): {str(detail)[:500]}"
        )
    assert result is not None  # sin `result` tampoco había `data`
    usage = result.get("usage") or {}
    return data, {
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "cost_usd": result.get("total_cost_usd"),
    }


PROVIDER = Provider("cli", ANTHROPIC_FORMAT, call)
