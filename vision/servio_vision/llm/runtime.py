"""Lo común de llamar al modelo, sea cual sea el proveedor: armar el pedido, el
cortacircuito, el tope de llamadas por día, el reintento y el reporte validado.

`make_estimator` devuelve el `PlateEstimator` del proveedor elegido (`registry.py`);
cada proveedor, en su archivo, solo hace la llamada."""

import base64
import logging
import threading
import time
from collections.abc import Callable
from typing import TypeVar

from pydantic import ValidationError

from servio_vision import prompt, settings
from servio_vision.classifiers.breaker import CircuitBreaker
from servio_vision.classifiers.usage import UsageMeter
from servio_vision.llm import registry
from servio_vision.llm.base import (
    ANTHROPIC_FORMAT,
    ClaudeCallError,
    EstimateRequest,
    Format,
    PermanentCallError,
    PlatesRequest,
)
from servio_vision.llm.parsing import (
    PlatesReport,
    TableEstimate,
    parse_plates,
    parse_table,
)

logger = logging.getLogger(__name__)

RETRY_WAIT_S = 2.0
ATTEMPTS = 2  # un intento + un reintento


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


def reset_state() -> None:
    """Cortacircuitos cerrados y contador en cero (para los tests)."""
    reset_breakers()
    reset_meter()


def _resolve(
    provider: str | None, model: str | None, timeout_s: float | None
) -> tuple[settings.Settings, str, str, float]:
    """Proveedor, modelo y timeout: lo que no vino por parámetro (app.py lo pasa todo)
    sale de la configuración."""
    cfg = settings.current()  # barato: la configuración activa o la del entorno
    provider = provider or cfg.claude_provider
    if provider not in registry.PROVIDERS:
        raise ValueError(
            f"CLAUDE_PROVIDER desconocido: {provider!r}. "
            f"Opciones: {sorted(registry.PROVIDERS)}"
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
    backend = registry.PROVIDERS[provider].call
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


class ProviderEstimator:
    """El `PlateEstimator` de un proveedor del registro, con lo común de llamarlo: el
    cortacircuito, el tope por día y el reintento. Lo que no se pasa sale de la
    configuración; el proveedor se elige al crearlo, así un valor inválido falla ya."""

    def __init__(
        self,
        provider: str | None = None,
        model: str | None = None,
        timeout_s: float | None = None,
    ) -> None:
        self._cfg, self.provider, self.model, self.timeout_s = _resolve(
            provider, model, timeout_s
        )

    @property
    def _format(self) -> Format:
        return registry.PROVIDERS[self.provider].format

    def estimate(self, request: EstimateRequest) -> PlatesReport | None:
        """Estima `fill_ratio`, `confidence`, `visible` y `same_plate` de cada plato.

        `request.references`: por plato, su recorte recién servido (o `None`), ver
        `build_content`. `None` si el modelo falla dos veces (el `hybrid` usa entonces
        lo de YOLO)."""
        jpegs = request.plate_jpegs
        if not jpegs:
            return PlatesReport(plates=[], people_present=True)
        # Una sola vez y fuera del loop: las imágenes no se recodifican en cada intento, y
        # un `references` de otro largo es un bug del que llama (ValueError), no un fallo
        # del modelo. Se normaliza acá y lo usan igual las imágenes y las preguntas.
        references = request.references or [None] * len(jpegs)
        content = build_content(jpegs, request.table_id, references, self._format)
        return self._call(
            PlatesRequest(content, [r is not None for r in references]),
            request.table_id,
            lambda data: parse_plates(data, len(jpegs)),
        )

    def estimate_table(self, table_jpeg: bytes, table_id: str) -> TableEstimate | None:
        """Los platos de una mesa entera, ubicados en la grilla 3×3 (clasificador `claude`).

        **Manda la imagen de la mesa**: quien llama tiene que haber pixelado a las
        personas y haber verificado que se permite (`CLAUDE_ALLOW_TABLE_IMAGES`, C-3).
        `None` si el modelo falla, igual que `estimate`."""
        fmt = self._format
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
        return self._call(request, table_id, parse_table)

    def _call(
        self, request: PlatesRequest, table_id: str, parse: Callable[[dict], _Parsed]
    ) -> _Parsed | None:
        return _call_model(
            request,
            provider=self.provider,
            model=self.model,
            timeout_s=self.timeout_s,
            cap=self._cfg.claude_max_calls_per_day,
            table_id=table_id,
            parse=parse,
        )


def make_estimator(
    provider: str | None = None,
    model: str | None = None,
    timeout_s: float | None = None,
) -> ProviderEstimator:
    """El estimador de platos del proveedor elegido (por defecto, el de la configuración)."""
    return ProviderEstimator(provider, model, timeout_s)
