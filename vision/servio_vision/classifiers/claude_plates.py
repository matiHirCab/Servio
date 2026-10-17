"""Claude por plato (TOM-3): estima cuánta comida queda en cada plato de una mesa.

Recibe el recorte de la mesa y los recortes de cada plato (JPEG) y devuelve, por
plato, `fill_ratio`, `confidence` y `visible`, más `people_present`.

Proveedor según `CLAUDE_PROVIDER`:
- `cli` (por defecto): el Claude Code instalado y logueado (`claude -p`), con la
  suscripción del equipo. No necesita API key.
- `anthropic`: la API de Anthropic con `ANTHROPIC_API_KEY` (respaldo; `pip install anthropic`).

Variables opcionales: `CLAUDE_MODEL` (por defecto `haiku` / `claude-haiku-5-5`),
`CLAUDE_TIMEOUT_S` (por defecto 15 s con `cli`, 10 s con `anthropic`), `CLAUDE_CLI`
(ruta a `claude.exe` si no está en el PATH).

Prueba rápida: `python -m servio_vision.classifiers.claude_plates mesa.jpg plato1.jpg plato2.jpg`
"""

import base64
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from servio_vision import prompt

logger = logging.getLogger(__name__)

DEFAULT_MODELS = {"cli": "haiku", "anthropic": "claude-haiku-5-5"}
# `claude -p` tarda ~6-8 s con 8 platos (arranque del CLI incluido).
DEFAULT_TIMEOUTS = {"cli": 15.0, "anthropic": 10.0}
ATTEMPTS = 2  # un intento + un reintento


class PlateEstimate(BaseModel):
    index: int = Field(ge=1, description="Número del recorte, desde 1.")
    fill_ratio: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    visible: bool = True


class PlatesReport(BaseModel):
    plates: list[PlateEstimate]
    people_present: bool = True


class ClaudeCallError(RuntimeError):
    """La llamada a Claude falló o devolvió algo que no se pudo leer."""


def _image_block(jpeg: bytes) -> dict:
    data = base64.standard_b64encode(jpeg).decode()
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/jpeg", "data": data},
    }


def build_content(table_jpeg: bytes, plate_jpegs: list[bytes], table_id: str) -> list:
    """Bloques del mensaje (texto + imágenes), iguales para los dos proveedores."""
    content: list[dict] = [
        {"type": "text", "text": prompt.table_caption(table_id)},
        _image_block(table_jpeg),
    ]
    for i, jpeg in enumerate(plate_jpegs, start=1):
        content.append({"type": "text", "text": prompt.plate_caption(i)})
        content.append(_image_block(jpeg))
    content.append({"type": "text", "text": prompt.closing_text(len(plate_jpegs))})
    return content


def _find_cli() -> str:
    # En Windows hay que llamar a claude.exe: claude.cmd rompe los argumentos.
    exe = (
        os.environ.get("CLAUDE_CLI")
        or shutil.which("claude.exe")
        or shutil.which("claude")
    )
    if not exe:
        raise ClaudeCallError(
            "No encuentro el comando claude (instalá Claude Code o usá CLAUDE_CLI)"
        )
    return exe


def _call_cli(content: list, model: str, timeout_s: float) -> tuple[dict, dict]:
    """`claude -p` con las imágenes por stdin (stream-json) y salida con esquema."""
    message = {
        "type": "user",
        "message": {"role": "user", "content": content},
    }
    args = [
        _find_cli(),
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
        "--system-prompt",
        prompt.SYSTEM_PROMPT,
        "--json-schema",
        json.dumps(prompt.REPORT_PLATES_SCHEMA),
        "--tools",
        "",
        "--no-session-persistence",
    ]
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
    if out.returncode != 0 or result is None or result.get("is_error"):
        detail = (result or {}).get("result") or out.stderr or out.stdout
        raise ClaudeCallError(f"claude -p falló: {str(detail)[:500]}")
    data = result.get("structured_output")
    if data is None:  # versiones viejas: el JSON viene como texto
        try:
            data = json.loads(result.get("result") or "")
        except json.JSONDecodeError as e:
            raise ClaudeCallError("claude -p no devolvió JSON") from e
    usage = result.get("usage") or {}
    return data, {
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "cost_usd": result.get("total_cost_usd"),
    }


def _call_anthropic(content: list, model: str, timeout_s: float) -> tuple[dict, dict]:
    """API de Anthropic con salida estructurada (`output_config.format`)."""
    import anthropic

    client = anthropic.Anthropic(timeout=timeout_s, max_retries=0)
    try:
        resp = client.messages.create(
            model=model,
            max_tokens=2048,
            system=prompt.SYSTEM_PROMPT,
            messages=[{"role": "user", "content": content}],
            output_config={
                "format": {
                    "type": "json_schema",
                    "schema": prompt.REPORT_PLATES_SCHEMA,
                },
                "effort": "low",
            },
        )
    except anthropic.APIError as e:
        raise ClaudeCallError(f"API de Anthropic: {e}") from e
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


_BACKENDS = {"cli": _call_cli, "anthropic": _call_anthropic}


def _parse(data: dict, n_plates: int) -> PlatesReport:
    report = PlatesReport.model_validate(data)
    indices = sorted(p.index for p in report.plates)
    if indices != list(range(1, n_plates + 1)):
        raise ValueError(f"esperaba platos 1..{n_plates}, llegaron {indices}")
    report.plates.sort(key=lambda p: p.index)
    return report


def estimate_plates(
    table_jpeg: bytes,
    plate_jpegs: list[bytes],
    table_id: str,
    *,
    provider: str | None = None,
    model: str | None = None,
    timeout_s: float | None = None,
) -> PlatesReport | None:
    """Estima `fill_ratio`, `confidence` y `visible` de cada plato.

    Devuelve `None` si Claude falla dos veces (el `hybrid` usa entonces lo de YOLO).
    """
    if not plate_jpegs:
        return PlatesReport(plates=[], people_present=True)
    provider = provider or os.environ.get("CLAUDE_PROVIDER") or "cli"
    if provider not in _BACKENDS:
        raise ValueError(
            f"CLAUDE_PROVIDER desconocido: {provider!r}. Opciones: {sorted(_BACKENDS)}"
        )
    model = model or os.environ.get("CLAUDE_MODEL") or DEFAULT_MODELS[provider]
    if timeout_s is None:
        timeout_s = float(
            os.environ.get("CLAUDE_TIMEOUT_S") or DEFAULT_TIMEOUTS[provider]
        )
    content = build_content(table_jpeg, plate_jpegs, table_id)

    for attempt in range(1, ATTEMPTS + 1):
        start = time.monotonic()
        try:
            data, usage = _BACKENDS[provider](content, model, timeout_s)
            report = _parse(data, len(plate_jpegs))
        except (ClaudeCallError, ValidationError, ValueError) as e:
            logger.warning(
                "Claude falló en la mesa %s (intento %d/%d): %s",
                table_id,
                attempt,
                ATTEMPTS,
                e,
            )
            continue
        logger.info(
            "Claude mesa=%s platos=%d proveedor=%s modelo=%s latencia=%.2fs uso=%s",
            table_id,
            len(plate_jpegs),
            provider,
            model,
            time.monotonic() - start,
            usage,
        )
        return report
    return None


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(
            "Uso: python -m servio_vision.classifiers.claude_plates mesa.jpg plato1.jpg [plato2.jpg ...]"
        )
        return 2
    logging.basicConfig(level=logging.INFO)
    table, *plates = (Path(p).read_bytes() for p in argv)
    report = estimate_plates(table, plates, "T1")
    if report is None:
        print("Claude no devolvió una respuesta válida")
        return 1
    print(report.model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
