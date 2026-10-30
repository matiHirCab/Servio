"""Claude por plato (TOM-3): estima cuánta comida queda en cada plato de una mesa.

Dos caminos, con distinta privacidad (C-3, docs/legal-uruguay.md):

- `estimate_plates` (el normal, `hybrid`): recibe los recortes de cada plato (JPEG) y
  devuelve, por plato, `fill_ratio`, `confidence`, `visible` y `same_plate`, más
  `people_present`. **Al modelo van solo recortes de plato, nunca la mesa.** Cada plato
  puede llevar su referencia (el recorte recién servido) antes del actual.
- `estimate_table` (solo el clasificador `claude`): **manda la imagen de la mesa
  entera** y el modelo ubica cada plato. Es opt-in (`CLAUDE_ALLOW_TABLE_IMAGES`) y quien
  lo llama tiene que haber pixelado a las personas antes; ver `classifiers/claude.py`.

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

Cada proveedor vive en su archivo, en `providers/` (`cli.py`, `anthropic_api.py`,
`openai_api.py`), con el mismo `call(request, model, timeout_s)`. Acá quedan lo que es
de todos: armar el pedido, reintentar, el cortacircuito, el tope de llamadas y el
reporte validado. Sumar un proveedor es un archivo nuevo más una línea en `_BACKENDS`
(y en `_FORMATS` si arma los bloques distinto).

Prueba rápida: `python -m servio_vision.classifiers.claude_plates mesa.jpg plato1.jpg plato2.jpg`
"""

import base64
import logging
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, Field, ValidationError

from servio_vision import prompt, settings
from servio_vision.classifiers.breaker import CircuitBreaker
from servio_vision.classifiers.providers import anthropic_api, cli, openai_api
from servio_vision.classifiers.providers.base import (
    ANTHROPIC_FORMAT,
    OPENAI_FORMAT,
    ClaudeCallError,
    Format,
    PermanentCallError,
    PlatesRequest,
)
from servio_vision.classifiers.usage import UsageMeter
from servio_vision.tracking import POSITION_LABELS

logger = logging.getLogger(__name__)

# Modelo y timeout por defecto de cada proveedor: settings.py (DEFAULT_MODELS,
# DEFAULT_TIMEOUTS). Acá se reexportan para quien ya los importaba de este módulo.
DEFAULT_MODELS = settings.DEFAULT_MODELS
DEFAULT_TIMEOUTS = settings.DEFAULT_TIMEOUTS
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


class TablePlate(BaseModel):
    """Un plato de la mesa entera: el modelo lo ubica en la grilla 3×3."""

    position: str
    fill_ratio: float = Field(ge=0, le=1)
    confidence: float = Field(ge=0, le=1)
    visible: bool = True


class TableEstimate(BaseModel):
    plates: list[TablePlate]
    people_present: bool = True


# Un cortacircuito por proveedor: con la API caída, las lecturas siguientes no gastan
# dos intentos y 2 s por mesa (hybrid usa lo de YOLO). Ver classifiers/breaker.py.
_clock = time.monotonic  # los tests lo reemplazan
_breakers: dict[str, CircuitBreaker] = {}
_breakers_lock = threading.Lock()


def _breaker(provider: str) -> CircuitBreaker:
    with _breakers_lock:
        if provider not in _breakers:

            def report(state: str) -> None:
                if state == "open":
                    logger.error(
                        "Claude (%s) falla seguido: dejo de llamarlo un rato y uso "
                        "YOLO solo",
                        provider,
                    )
                else:
                    logger.info("Claude (%s) volvió a responder", provider)

            _breakers[provider] = CircuitBreaker(
                clock=lambda: _clock(), on_change=report
            )
        return _breakers[provider]


def reset_breakers() -> None:
    """Todos cerrados (para los tests)."""
    with _breakers_lock:
        _breakers.clear()


# Cuánto se llamó hoy y el tope (`CLAUDE_MAX_CALLS_PER_DAY`). Ver classifiers/usage.py.
_meter = UsageMeter()


def reset_meter() -> None:
    """El contador en cero (para los tests)."""
    global _meter
    _meter = UsageMeter()


# Cómo arma los bloques cada proveedor (el `cli` y `anthropic` comparten formato).
_FORMATS = {
    "cli": ANTHROPIC_FORMAT,
    "anthropic": ANTHROPIC_FORMAT,
    "openai": OPENAI_FORMAT,
}


def build_content(
    plate_jpegs: list[bytes],
    table_id: str,
    references: list[bytes | None] | None = None,
    fmt: Format = ANTHROPIC_FORMAT,
) -> list:
    """Bloques del mensaje (texto + imágenes) en el formato `fmt`.

    Al proveedor van **solo los recortes de plato** (C-3, docs/legal-uruguay.md),
    nunca la mesa. `references[i]`, si está, es el plato i recién servido y va antes
    del recorte actual."""

    def image(jpeg: bytes) -> dict:
        return fmt.image(base64.standard_b64encode(jpeg).decode())

    content: list[dict] = [fmt.text(prompt.table_caption(table_id))]
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


_BACKENDS = {
    "cli": cli.call,
    "anthropic": anthropic_api.call,
    "openai": openai_api.call,
}


def _parse(data: dict, n_plates: int) -> PlatesReport:
    report = PlatesReport.model_validate(data)
    indices = sorted(p.index for p in report.plates)
    if indices != list(range(1, n_plates + 1)):
        raise ValueError(f"esperaba platos 1..{n_plates}, llegaron {indices}")
    report.plates.sort(key=lambda p: p.index)
    return report


def _parse_table(data: dict) -> TableEstimate:
    estimate = TableEstimate.model_validate(data)
    positions = [p.position for p in estimate.plates]
    unknown = sorted(set(positions) - set(POSITION_LABELS))
    if unknown:
        raise ValueError(f"posiciones que no existen: {unknown}")
    repeated = sorted({p for p in positions if positions.count(p) > 1})
    if repeated:  # dos platos en la misma celda: no se pueden seguir por posición
        raise ValueError(f"dos platos en la misma posición: {repeated}")
    return estimate


def _resolve(
    provider: str | None, model: str | None, timeout_s: float | None
) -> tuple[settings.Settings, str, str, float]:
    """Proveedor, modelo y timeout: lo que no vino por parámetro (app.py lo pasa todo)
    sale de la configuración."""
    cfg = settings.current()  # barato: la configuración activa o la del entorno
    provider = provider or cfg.claude_provider
    if provider not in _BACKENDS:
        raise ValueError(
            f"CLAUDE_PROVIDER desconocido: {provider!r}. Opciones: {sorted(_BACKENDS)}"
        )
    model = model or cfg.model_for(provider)
    timeout_s = cfg.timeout_for(provider) if timeout_s is None else timeout_s
    return cfg, provider, model, timeout_s


_Parsed = TypeVar("_Parsed")


def _call_model(
    request: PlatesRequest,
    *,
    provider: str,
    model: str,
    timeout_s: float,
    cap: int | None,
    table_id: str,
    parse: Callable[[dict], _Parsed],
) -> _Parsed | None:
    """La llamada con todo lo común: el cortacircuito, el tope de llamadas por día,
    el reintento y el conteo de uso. `parse` valida la respuesta (un error ahí se
    reintenta como uno del proveedor). `None` si no se pudo."""
    backend = _BACKENDS[provider]
    breaker = _breaker(provider)
    if not breaker.allow():
        logger.debug(
            "Claude (%s) en pausa: la mesa %s va sin llamada", provider, table_id
        )
        return None

    try:
        for attempt in range(1, ATTEMPTS + 1):
            # cada intento se factura, también el reintento
            if not _meter.try_start(cap):
                if _meter.first_cap_report():
                    logger.error(
                        "Se llegó al tope de %d llamadas al modelo por día "
                        "(CLAUDE_MAX_CALLS_PER_DAY): sigo con YOLO solo hasta mañana",
                        cap,
                    )
                if attempt == 1:  # no se llamó: que el cortacircuito no quede esperando
                    breaker.cancel_probe()
                    return None
                break  # el primer intento ya falló: cuenta como falla
            start = time.monotonic()
            try:
                data, usage = backend(request, model, timeout_s)
                report = parse(data)
            except (PermanentCallError, prompt.PromptError) as e:
                # una clave inválida o un prompt roto no se arreglan reintentando
                logger.error(
                    "Claude falló en la mesa %s y reintentar no lo arregla: %s",
                    table_id,
                    e,
                )
                breaker.record_failure(permanent=True)
                return None
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
            logger.debug(
                "Claude mesa=%s proveedor=%s modelo=%s latencia=%.2fs uso=%s",
                table_id,
                provider,
                model,
                time.monotonic() - start,
                usage,
            )
            _meter.add_usage(usage)
            if _meter.report_due():
                logger.info("Uso del modelo: %s", _meter.summary())
            breaker.record_success()
            return report
    except Exception:
        # un bug nuestro: que la llamada de prueba no deje el cortacircuito trabado
        breaker.record_failure()
        raise
    breaker.record_failure()
    return None


def estimate_plates(
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
    cfg, provider, model, timeout_s = _resolve(provider, model, timeout_s)
    # Una sola vez y fuera del loop: las imágenes no se recodifican en cada intento, y un
    # `references` de otro largo es un bug del que llama (ValueError), no un fallo de Claude.
    # `references` se normaliza acá y lo usan igual las imágenes y las preguntas.
    references = references or [None] * len(plate_jpegs)
    content = build_content(plate_jpegs, table_id, references, _FORMATS[provider])
    request = PlatesRequest(content, [r is not None for r in references])
    return _call_model(
        request,
        provider=provider,
        model=model,
        timeout_s=timeout_s,
        cap=cfg.claude_max_calls_per_day,
        table_id=table_id,
        parse=lambda data: _parse(data, len(plate_jpegs)),
    )


def estimate_table(
    table_jpeg: bytes,
    table_id: str,
    *,
    provider: str | None = None,
    model: str | None = None,
    timeout_s: float | None = None,
) -> TableEstimate | None:
    """Los platos de una mesa entera, ubicados en la grilla 3×3 (clasificador `claude`).

    **Manda la imagen de la mesa**: quien llama tiene que haber pixelado a las
    personas y haber verificado que se permite (`CLAUDE_ALLOW_TABLE_IMAGES`, C-3).
    `None` si el modelo falla, igual que `estimate_plates`."""
    cfg, provider, model, timeout_s = _resolve(provider, model, timeout_s)
    fmt = _FORMATS[provider]
    content = [
        fmt.text(prompt.table_whole_caption(table_id)),
        fmt.image(base64.standard_b64encode(table_jpeg).decode()),
    ]
    request = PlatesRequest(
        content,
        [],
        system=prompt.table_system_prompt(),
        schema=prompt.REPORT_TABLE_SCHEMA,
    )
    return _call_model(
        request,
        provider=provider,
        model=model,
        timeout_s=timeout_s,
        cap=cfg.claude_max_calls_per_day,
        table_id=table_id,
        parse=_parse_table,
    )


def main(argv: list[str]) -> int:
    if not argv:
        print(
            "Uso: python -m servio_vision.classifiers.claude_plates plato1.jpg [plato2.jpg ...]"
        )
        return 2
    logging.basicConfig(level=logging.INFO)
    plates = [Path(p).read_bytes() for p in argv]
    report = estimate_plates(plates, "T1")
    if report is None:
        print("Claude no devolvió una respuesta válida")
        return 1
    print(report.model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
