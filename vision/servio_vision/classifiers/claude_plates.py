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

Variables opcionales: `CLAUDE_MODEL` (por defecto `haiku` / `claude-haiku-5-5`), `CLAUDE_TIMEOUT_S`
(por defecto 15 s con `cli`, 10 s con `anthropic`), `CLAUDE_CLI` (ruta a `claude.exe` si no está en
el PATH). Con `openai`: `OPENAI_MODEL` (por defecto `gpt-6-luna`) y `OPENAI_TIMEOUT_S` (por defecto
14 s); van aparte para que un valor pensado para Claude no termine en OpenAI.

Prueba rápida: `python -m servio_vision.classifiers.claude_plates mesa.jpg plato1.jpg plato2.jpg`
"""

import base64
import json
import logging
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from servio_vision import prompt

logger = logging.getLogger(__name__)

DEFAULT_MODELS = {
    "cli": "haiku",
    "anthropic": "claude-haiku-5-5",
    "openai": "gpt-6-luna",
}
MODEL_ENV = {
    "cli": "CLAUDE_MODEL",
    "anthropic": "CLAUDE_MODEL",
    "openai": "OPENAI_MODEL",
}
# `claude -p` tarda ~6-8 s con 8 platos (arranque del CLI incluido).
# openai: unas 25 preguntas tipadas y ~17 imágenes en una mesa de 8 platos con referencias.
# Con el reintento, el peor caso es 2 × timeout + RETRY_WAIT_S: 14 s deja 30 s, el
# --interval por defecto, para no atrasar la lectura siguiente.
DEFAULT_TIMEOUTS = {"cli": 15.0, "anthropic": 10.0, "openai": 14.0}
TIMEOUT_ENV = {
    "cli": "CLAUDE_TIMEOUT_S",
    "anthropic": "CLAUDE_TIMEOUT_S",
    "openai": "OPENAI_TIMEOUT_S",
}
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
    """Lo que se manda por una mesa: el contenido ya armado y, por plato, si lleva
    referencia (la Decisions API pregunta same_plate solo para esos)."""

    content: list
    has_reference: list[bool]


@dataclass(frozen=True)
class _Format:
    """Cómo arma los bloques cada API."""

    text: Callable[[str], dict]
    image: Callable[[str], dict]  # recibe la imagen en base64
    closing: bool  # agrega el cierre que pide el JSON


ANTHROPIC_FORMAT = _Format(
    text=lambda text: {"type": "text", "text": text},
    image=lambda data: {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/jpeg", "data": data},
    },
    closing=True,
)
# La Decisions API responde preguntas tipadas: sin el cierre que pide JSON.
OPENAI_FORMAT = _Format(
    text=lambda text: {"type": "input_text", "text": text},
    image=lambda data: {
        "type": "input_image",
        "image_url": f"data:image/jpeg;base64,{data}",
    },
    closing=False,
)
_FORMATS = {
    "cli": ANTHROPIC_FORMAT,
    "anthropic": ANTHROPIC_FORMAT,
    "openai": OPENAI_FORMAT,
}


def build_content(
    table_jpeg: bytes,
    plate_jpegs: list[bytes],
    table_id: str,
    references: list[bytes | None] | None = None,
    fmt: _Format = ANTHROPIC_FORMAT,
) -> list:
    """Bloques del mensaje (texto + imágenes) en el formato `fmt`.

    `references[i]`, si está, es el plato i recién servido y va antes del recorte actual."""

    def image(jpeg: bytes) -> dict:
        return fmt.image(base64.standard_b64encode(jpeg).decode())

    content: list[dict] = [fmt.text(prompt.table_caption(table_id)), image(table_jpeg)]
    references = references or [None] * len(plate_jpegs)
    # strict: un plato sin su lugar en `references` no se manda en silencio
    for i, (jpeg, ref) in enumerate(zip(plate_jpegs, references, strict=True), start=1):
        if ref is None:
            content.append(fmt.text(prompt.plate_caption(i)))
        else:
            content.append(fmt.text(prompt.reference_caption(i)))
            content.append(image(ref))
            content.append(fmt.text(prompt.current_caption(i)))
        content.append(image(jpeg))
    if fmt.closing:
        content.append(fmt.text(prompt.closing_text(len(plate_jpegs))))
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


def _call_cli(
    request: PlatesRequest, model: str, timeout_s: float
) -> tuple[dict, dict]:
    """`claude -p` con las imágenes por stdin (stream-json).

    El prompt va dentro del mensaje y no como argumento: en Windows, si `claude` es
    un `.cmd`, los argumentos de varias líneas o con comillas llegan rotos.
    """
    exe = _find_cli()
    instructions = ANTHROPIC_FORMAT.text(prompt.SYSTEM_PROMPT)
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
        raise ClaudeCallError(
            "falta el paquete anthropic: pip install anthropic"
        ) from e

    client = anthropic.Anthropic(timeout=timeout_s, max_retries=0)
    try:
        resp = client.messages.create(
            model=model,
            max_tokens=2048,
            system=prompt.SYSTEM_PROMPT,
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


FILL_RATIO_BY_LABEL = {
    level["label"]: ratio
    for level, ratio in zip(prompt.FILL_LEVELS, prompt.FILL_RATIOS)
}
# Las probabilities de un score tienen que sumar ~1; si no, la lista vino incompleta.
PROBABILITIES_SUM_TOLERANCE = 0.05


def _get(obj, field: str):
    """Campo de un objeto del SDK o de un dict."""
    return obj.get(field) if isinstance(obj, dict) else getattr(obj, field, None)


def _number(value, what: str, low: float, high: float) -> float:
    """Valida un número de la respuesta: finito y dentro de [low, high]."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ClaudeCallError(f"{what} no es un número: {value!r}")
    if not math.isfinite(value) or not low <= value <= high:
        raise ClaudeCallError(f"{what} fuera de {low}..{high}: {value!r}")
    return float(value)


def _fill_ratio(answer, name: str) -> float:
    """Respuesta de score → fill_ratio.

    Con `probabilities` (una por nivel) es el valor esperado sobre `FILL_RATIOS`, que no
    está equiespaciado: 50% vacío y 50% intacto da 0.5. Si no vienen, o vienen
    incompletas, se interpola `score` (promedio de los índices de nivel ponderado por
    probabilidad, desde 0). Una entrada mal formada es `ClaudeCallError`, como el resto."""
    top = len(prompt.FILL_LEVELS) - 1
    # fuera de 0..top es otra escala y no se recorta: mejor caer a YOLO que avisar mal
    score = _number(_get(answer, "score"), f"{name}.score", 0, top)
    probabilities = _get(answer, "probabilities")
    if probabilities is not None:
        if not isinstance(probabilities, (list, tuple)):
            raise ClaudeCallError(
                f"{name}.probabilities no es una lista: {probabilities!r}"
            )
        total = expected = 0.0
        for entry in probabilities:
            label = _get(entry, "label")
            if label not in FILL_RATIO_BY_LABEL:
                raise ClaudeCallError(f"{name}: nivel desconocido {label!r}")
            p = _number(_get(entry, "probability"), f"{name}.{label}", 0, 1)
            total += p
            expected += p * FILL_RATIO_BY_LABEL[label]
        if abs(total - 1) <= PROBABILITIES_SUM_TOLERANCE:
            return expected / total
        logger.warning("%s: las probabilities suman %.2f, uso el score", name, total)
    low = min(int(score), top - 1)
    frac = score - low
    return prompt.FILL_RATIOS[low] * (1 - frac) + prompt.FILL_RATIOS[low + 1] * frac


def _answers_to_report(
    answers: list, questions: list[dict], has_reference: list[bool]
) -> dict:
    """Respuestas tipadas → el mismo dict que devuelven los otros proveedores.

    Cualquier respuesta con forma rara es `ClaudeCallError`, así se reintenta y, si
    vuelve a fallar, la mesa usa lo de YOLO en vez de cortar el pipeline."""
    expected = {q["name"] for q in questions}
    by_name = {}
    for answer in answers:
        name = _get(answer, "name")
        if _get(answer, "type") == "refusal":
            raise ClaudeCallError(f"la Decisions API rechazó {name!r}")
        if name not in expected:
            raise ClaudeCallError(
                f"la Decisions API respondió algo no pedido: {name!r}"
            )
        if name in by_name:
            raise ClaudeCallError(f"la Decisions API respondió dos veces {name!r}")
        by_name[name] = answer

    def get(name: str):
        if name not in by_name:
            raise ClaudeCallError(f"la Decisions API no respondió {name!r}")
        return by_name[name]

    def yes(name: str) -> bool:
        probability = _get(get(name), "probability")
        return _number(probability, f"{name}.probability", 0, 1) >= 0.5

    plates = []
    for i, ref in enumerate(has_reference, start=1):
        fill = get(f"fill_{i}")
        plates.append(
            {
                "index": i,
                "fill_ratio": _fill_ratio(fill, f"fill_{i}"),
                # el rango lo valida _parse (pydantic)
                "confidence": _number(
                    _get(fill, "confidence"),
                    f"fill_{i}.confidence",
                    -math.inf,
                    math.inf,
                ),
                "visible": yes(f"visible_{i}"),
                "same_plate": yes(f"same_plate_{i}") if ref else True,
            }
        )
    return {"plates": plates, "people_present": yes("people_present")}


def _call_openai(
    request: PlatesRequest, model: str, timeout_s: float
) -> tuple[dict, dict]:
    """Decisions API de OpenAI (`POST /v1/decisions`) con `OPENAI_API_KEY`.

    El prompt va primero como texto (la API no tiene system prompt) y sin el pedido
    de JSON: acá se responden preguntas tipadas."""
    try:
        import openai
    except ImportError as e:
        raise ClaudeCallError(
            'falta el paquete openai: pip install "openai>=3.26.0"'
        ) from e

    parts = [OPENAI_FORMAT.text(prompt.INSTRUCTIONS), *request.content]
    # las preguntas same_plate_i solo van para los platos con referencia
    questions = prompt.decision_questions(request.has_reference)
    client = openai.OpenAI(timeout=timeout_s, max_retries=0)
    if not hasattr(client, "decisions"):
        raise ClaudeCallError(
            'el paquete openai es viejo para la Decisions API: pip install "openai>=3.26.0"'
        )
    try:
        resp = client.decisions.create(
            model=model,
            input=[{"role": "user", "content": parts}],
            questions=questions,
        )
    except openai.OpenAIError as e:
        raise ClaudeCallError(f"Decisions API de OpenAI: {e}") from e
    usage = getattr(resp, "usage", None)
    answers = getattr(resp, "answers", None) or []
    return _answers_to_report(answers, questions, request.has_reference), {
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

    `references`: por plato, su recorte recién servido (o `None`), ver `build_content`.
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
            os.environ.get(TIMEOUT_ENV[provider]) or DEFAULT_TIMEOUTS[provider]
        )
    # Una sola vez y fuera del loop: las imágenes no se recodifican en cada intento, y un
    # `references` de otro largo es un bug del que llama (ValueError), no un fallo de Claude.
    # `references` se normaliza acá y lo usan igual las imágenes y las preguntas.
    references = references or [None] * len(plate_jpegs)
    content = build_content(
        table_jpeg, plate_jpegs, table_id, references, _FORMATS[provider]
    )
    request = PlatesRequest(content, [r is not None for r in references])
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
