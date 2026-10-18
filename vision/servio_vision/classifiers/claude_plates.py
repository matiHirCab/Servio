"""Claude por plato (TOM-3): estima cuánta comida queda en cada plato de una mesa.

Recibe el recorte de la mesa y los recortes de cada plato (JPEG) y devuelve, por
plato, `fill_ratio`, `confidence`, `visible` y `same_plate`, más `people_present`.
Cada plato puede llevar su referencia (el recorte recién servido) antes del actual.

Proveedor según `CLAUDE_PROVIDER`:
- `cli` (por defecto): el Claude Code instalado y logueado (`claude -p`), con la
  suscripción del equipo. No necesita API key.
- `anthropic`: la API de Anthropic con `ANTHROPIC_API_KEY` (respaldo; `pip install anthropic`).
- `openai`: la Decisions API de OpenAI con `OPENAI_API_KEY` (`pip install "openai>=3.26.0"`).
  En vez de un JSON libre hace preguntas tipadas por plato (ver `prompt.decision_questions`).

Variables opcionales: `CLAUDE_MODEL` (por defecto `haiku` / `claude-haiku-5-5`), `OPENAI_MODEL`
(por defecto `gpt-6-luna`; separado para que un modelo de Claude no termine en OpenAI), `CLAUDE_TIMEOUT_S` (por defecto 15 s con `cli`, 10 s con `anthropic` y `openai`), `CLAUDE_CLI`
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
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from servio_vision import prompt

logger = logging.getLogger(__name__)

DEFAULT_MODELS = {"cli": "haiku", "anthropic": "claude-haiku-5-5", "openai": "gpt-6-luna"}
MODEL_ENV = {"cli": "CLAUDE_MODEL", "anthropic": "CLAUDE_MODEL", "openai": "OPENAI_MODEL"}
# `claude -p` tarda ~6-8 s con 8 platos (arranque del CLI incluido).
DEFAULT_TIMEOUTS = {"cli": 15.0, "anthropic": 10.0, "openai": 10.0}
RETRY_WAIT_S = 2.0
ATTEMPTS = 2  # un intento + un reintento


class PlateEstimate(BaseModel):
    index: int = Field(ge=1, description="Número del recorte, desde 1.")
    fill_ratio: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    visible: bool = True
    same_plate: bool = True  # False: la referencia es de otra comida (id cambiado)


class PlatesReport(BaseModel):
    plates: list[PlateEstimate]
    people_present: bool = True


class ClaudeCallError(RuntimeError):
    """La llamada a Claude falló o devolvió algo que no se pudo leer."""


@dataclass(frozen=True)
class PlatesRequest:
    """Lo que se le pregunta a Claude por una mesa. `references[i]`, si está, es el
    plato i recién servido y va antes del recorte actual."""

    table_jpeg: bytes
    plate_jpegs: list[bytes]
    table_id: str
    references: list[bytes | None] | None = None

    @property
    def has_reference(self) -> list[bool]:
        return [r is not None for r in self.references or [None] * len(self.plate_jpegs)]


def _image_block(jpeg: bytes, openai: bool = False) -> dict:
    data = base64.standard_b64encode(jpeg).decode()
    if openai:
        return {"type": "input_image", "image_url": f"data:image/jpeg;base64,{data}"}
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/jpeg", "data": data},
    }


def _text_block(text: str, openai: bool = False) -> dict:
    return {"type": "input_text" if openai else "text", "text": text}


def build_content(request: PlatesRequest, *, openai: bool = False) -> list:
    """Bloques del mensaje (texto + imágenes) en el formato de Anthropic o, con
    `openai=True`, en el de la Decisions API (sin el cierre que pide JSON)."""
    content: list[dict] = [
        _text_block(prompt.table_caption(request.table_id), openai),
        _image_block(request.table_jpeg, openai),
    ]
    plate_jpegs = request.plate_jpegs
    references = request.references or [None] * len(plate_jpegs)
    # strict: un plato sin su lugar en `references` no se manda en silencio
    for i, (jpeg, ref) in enumerate(zip(plate_jpegs, references, strict=True), start=1):
        if ref is None:
            content.append(_text_block(prompt.plate_caption(i), openai))
        else:
            content.append(_text_block(prompt.reference_caption(i), openai))
            content.append(_image_block(ref, openai))
            content.append(_text_block(prompt.current_caption(i), openai))
        content.append(_image_block(jpeg, openai))
    if not openai:
        content.append(_text_block(prompt.closing_text(len(plate_jpegs))))
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


def json_from_text(text: str) -> dict:
    """Saca el objeto JSON de una respuesta de texto (puede venir entre ``` o con texto)."""
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            pass
    raise ClaudeCallError(f"claude -p no devolvió JSON: {text[:300]!r}")


def _call_cli(request: PlatesRequest, model: str, timeout_s: float) -> tuple[dict, dict]:
    """`claude -p` con las imágenes por stdin (stream-json).

    El prompt va dentro del mensaje y no como argumento: en Windows, si `claude` es
    un `.cmd`, los argumentos de varias líneas o con comillas llegan rotos.
    """
    exe = _find_cli()
    instructions = _text_block(prompt.SYSTEM_PROMPT)
    message = {
        "type": "user",
        "message": {"role": "user", "content": [instructions, *build_content(request)]},
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
        args += ["--json-schema", json.dumps(prompt.REPORT_PLATES_SCHEMA)]
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
            # sin esto, claude se actualiza solo en medio de la corrida y el
            # siguiente llamado no encuentra el ejecutable
            env={**os.environ, "DISABLE_AUTOUPDATER": "1"},
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
    usage = result.get("usage") or {}
    return data, {
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "cost_usd": result.get("total_cost_usd"),
    }


def _call_anthropic(
    request: PlatesRequest, model: str, timeout_s: float
) -> tuple[dict, dict]:
    """API de Anthropic con salida estructurada (`output_config.format`)."""
    try:
        import anthropic
    except ImportError as e:
        raise ClaudeCallError("falta el paquete anthropic: pip install anthropic") from e

    client = anthropic.Anthropic(timeout=timeout_s, max_retries=0)
    try:
        resp = client.messages.create(
            model=model,
            max_tokens=2048,
            system=prompt.SYSTEM_PROMPT,
            messages=[{"role": "user", "content": build_content(request)}],
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


def _answers_to_report(answers: list, has_reference: list[bool]) -> dict:
    """Respuestas tipadas → el mismo dict que devuelven los otros proveedores.

    Cualquier respuesta con forma rara es `ClaudeCallError`, así se reintenta y, si
    vuelve a fallar, la mesa usa lo de YOLO en vez de cortar el pipeline."""
    expected = {q["name"] for q in prompt.decision_questions(has_reference)}
    by_name = {}
    for answer in answers:
        name = getattr(answer, "name", None)
        if getattr(answer, "type", None) == "refusal":
            raise ClaudeCallError(f"la Decisions API rechazó {name!r}")
        if name not in expected:
            raise ClaudeCallError(f"la Decisions API respondió algo no pedido: {name!r}")
        if name in by_name:
            raise ClaudeCallError(f"la Decisions API respondió dos veces {name!r}")
        by_name[name] = answer

    def number(name: str, field: str) -> float:
        if name not in by_name:
            raise ClaudeCallError(f"la Decisions API no respondió {name!r}")
        value = getattr(by_name[name], field, None)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ClaudeCallError(f"{name}.{field} no es un número: {value!r}")
        return float(value)

    # score es el índice del nivel en FILL_LEVELS (0 = vacío); fuera de rango es
    # otra escala y no se recorta: mejor caer a YOLO que avisar mal.
    top = len(prompt.FILL_LEVELS) - 1
    plates = []
    for i, ref in enumerate(has_reference, start=1):
        score = number(f"fill_{i}", "score")
        if not 0 <= score <= top:
            raise ClaudeCallError(f"fill_{i}.score fuera de 0..{top}: {score}")
        plates.append(
            {
                "index": i,
                "fill_ratio": score / top,
                "confidence": number(f"fill_{i}", "confidence"),
                "visible": number(f"visible_{i}", "probability") >= 0.5,
                "same_plate": number(f"same_plate_{i}", "probability") >= 0.5
                if ref
                else True,
            }
        )
    return {
        "plates": plates,
        "people_present": number("people_present", "probability") >= 0.5,
    }


def _call_openai(request: PlatesRequest, model: str, timeout_s: float) -> tuple[dict, dict]:
    """Decisions API de OpenAI (`POST /v1/decisions`) con `OPENAI_API_KEY`.

    El prompt va primero como texto (la API no tiene system prompt) y sin el pedido
    de JSON: acá se responden preguntas tipadas."""
    try:
        import openai
    except ImportError as e:
        raise ClaudeCallError(
            'falta el paquete openai: pip install "openai>=3.26.0"'
        ) from e

    parts = [_text_block(prompt.INSTRUCTIONS, True), *build_content(request, openai=True)]
    client = openai.OpenAI(timeout=timeout_s, max_retries=0)
    try:
        resp = client.decisions.create(
            model=model,
            input=[{"role": "user", "content": parts}],
            # las preguntas same_plate_i solo van para los platos con referencia
            questions=prompt.decision_questions(request.has_reference),
        )
    except openai.OpenAIError as e:
        raise ClaudeCallError(f"Decisions API de OpenAI: {e}") from e
    usage = getattr(resp, "usage", None)
    return _answers_to_report(getattr(resp, "answers", None) or [], request.has_reference), {
        "input_tokens": getattr(usage, "input_tokens", None),
    }


_BACKENDS = {"cli": _call_cli, "anthropic": _call_anthropic, "openai": _call_openai}


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
    references: list[bytes | None] | None = None,
) -> PlatesReport | None:
    """Estima `fill_ratio`, `confidence`, `visible` y `same_plate` de cada plato.

    `references`: por plato, su recorte recién servido (o `None`), ver `PlatesRequest`.
    Devuelve `None` si Claude falla dos veces (el `hybrid` usa entonces lo de YOLO).
    """
    if not plate_jpegs:
        return PlatesReport(plates=[], people_present=True)
    provider = provider or os.environ.get("CLAUDE_PROVIDER") or "cli"
    if provider not in _BACKENDS:
        raise ValueError(
            f"CLAUDE_PROVIDER desconocido: {provider!r}. Opciones: {sorted(_BACKENDS)}"
        )
    model = model or os.environ.get(MODEL_ENV[provider]) or DEFAULT_MODELS[provider]
    if timeout_s is None:
        timeout_s = float(
            os.environ.get("CLAUDE_TIMEOUT_S") or DEFAULT_TIMEOUTS[provider]
        )
    request = PlatesRequest(table_jpeg, plate_jpegs, table_id, references)
    backend = _BACKENDS[provider]

    for attempt in range(1, ATTEMPTS + 1):
        start = time.monotonic()
        try:
            data, usage = backend(request, model, timeout_s)
            report = _parse(data, len(plate_jpegs))
        except (ClaudeCallError, ValidationError, ValueError) as e:
            logger.warning(
                "Claude falló en la mesa %s (intento %d/%d): %s",
                table_id,
                attempt,
                ATTEMPTS,
                e,
            )
            if attempt < ATTEMPTS:
                time.sleep(RETRY_WAIT_S)
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
