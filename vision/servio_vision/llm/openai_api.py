"""Proveedor `openai`: la Decisions API con `OPENAI_API_KEY`.

En vez de un JSON libre hace preguntas tipadas por plato (`prompt.decision_questions`)
y acá se traducen las respuestas al mismo dict que devuelven los otros proveedores.
"""

import logging
import math

from servio_vision import prompt, settings
from servio_vision.llm.base import (
    OPENAI_FORMAT,
    ClaudeCallError,
    PermanentCallError,
    PlatesRequest,
    Provider,
    api_error,
)

logger = logging.getLogger(__name__)


def _ratio_by_label() -> dict[str, float]:
    """label de cada nivel (de openai.json) → su fill_ratio."""
    return {
        level["label"]: ratio
        for level, ratio in zip(prompt.fill_levels(), prompt.FILL_RATIOS)
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
    top = len(prompt.FILL_RATIOS) - 1
    ratio_by_label = _ratio_by_label()
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
            if label not in ratio_by_label:
                raise ClaudeCallError(f"{name}: nivel desconocido {label!r}")
            p = _number(_get(entry, "probability"), f"{name}.{label}", 0, 1)
            total += p
            expected += p * ratio_by_label[label]
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


def call(request: PlatesRequest, model: str, timeout_s: float) -> tuple[dict, dict]:
    """Decisions API de OpenAI (`POST /v1/decisions`) con `OPENAI_API_KEY`.

    El prompt va primero como texto (la API no tiene system prompt) y sin el pedido
    de JSON: acá se responden preguntas tipadas."""
    if request.schema is not None:
        # la mesa entera (clasificador `claude`): la API pregunta por un número fijo
        # de platos y acá habría que encontrarlos primero
        raise PermanentCallError(
            "el proveedor openai no lee la mesa entera: usá cli o anthropic, o el "
            "clasificador hybrid"
        )
    try:
        import openai
    except ImportError as e:
        raise PermanentCallError(
            'falta el paquete openai: pip install "openai>=3.26.0"'
        ) from e

    parts = [OPENAI_FORMAT.text(prompt.openai_instructions()), *request.content]
    # las preguntas same_plate_i solo van para los platos con referencia
    questions = prompt.decision_questions(request.has_reference)
    client = openai.OpenAI(
        api_key=settings.current().openai_api_key or None,
        timeout=timeout_s,
        max_retries=0,
    )
    if not hasattr(client, "decisions"):
        raise PermanentCallError(
            'el paquete openai es viejo para la Decisions API: pip install "openai>=3.26.0"'
        )
    try:
        resp = client.decisions.create(
            model=model,
            input=[{"role": "user", "content": parts}],
            questions=questions,
        )
    except openai.OpenAIError as e:
        raise api_error("Decisions API de OpenAI", e) from e
    usage = getattr(resp, "usage", None)
    answers = getattr(resp, "answers", None) or []
    return _answers_to_report(answers, questions, request.has_reference), {
        "input_tokens": getattr(usage, "input_tokens", None),
    }


PROVIDER = Provider("openai", OPENAI_FORMAT, call)
