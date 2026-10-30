"""Cortacircuito para las llamadas al modelo.

Con la API caída o una clave inválida, cada lectura de cada mesa gastaba dos
intentos y 2 s de espera para nada, y `hybrid` caía a YOLO igual. El cortacircuito
cuenta las fallas seguidas: al llegar a `failures_to_open` se abre y las llamadas
fallan al instante (el que llama usa lo de YOLO) durante `cooldown_s`; pasado ese
tiempo deja pasar **una** de prueba. Si anda, se cierra; si falla, se vuelve a abrir
con el doble de espera (hasta `max_cooldown_s`). Un error permanente (clave
inválida, paquete que falta) lo abre de entrada con la espera más larga.
"""

import threading
import time
from collections.abc import Callable


class CircuitBreaker:
    def __init__(
        self,
        failures_to_open: int = 3,
        cooldown_s: float = 60.0,
        max_cooldown_s: float = 600.0,
        clock: Callable[[], float] = time.monotonic,
        on_change: Callable[[str], None] | None = None,
    ) -> None:
        """`on_change("open" | "closed")`: para loguear cuando cambia de estado."""
        self.failures_to_open = failures_to_open
        self.cooldown_s = cooldown_s
        self.max_cooldown_s = max_cooldown_s
        self._clock = clock
        self._on_change = on_change
        self._lock = threading.Lock()
        self._failures = 0
        self._opened_at: float | None = None  # None: cerrado
        self._wait = cooldown_s  # cuánto dura la espera actual
        self._probing = False

    def allow(self) -> bool:
        """¿Se puede llamar? Abierto: no, salvo una de prueba pasada la espera."""
        with self._lock:
            if self._opened_at is None:
                return True
            if self._probing or self._clock() - self._opened_at < self._wait:
                return False
            self._probing = True
            return True

    def cancel_probe(self) -> None:
        """La llamada de prueba no llegó a hacerse: que otro pueda probar."""
        with self._lock:
            self._probing = False

    def record_success(self) -> None:
        with self._lock:
            was_open = self._opened_at is not None
            self._failures = 0
            self._opened_at = None
            self._wait = self.cooldown_s
            self._probing = False
        if was_open:
            self._notify("closed")

    def record_failure(self, permanent: bool = False) -> None:
        with self._lock:
            self._failures += 1
            was_open = self._opened_at is not None
            if was_open and permanent:
                # una clave inválida que recién se supo, de una llamada que venía en
                # vuelo: a la espera más larga
                self._wait = self.max_cooldown_s
            elif was_open and self._probing:
                # falló la prueba: el doble de espera, hasta el tope
                self._wait = min(self._wait * 2, self.max_cooldown_s)
            elif was_open:
                # una llamada que ya venía en vuelo cuando se abrió: no es una prueba
                # y no prolonga la pausa
                return
            elif permanent:
                self._wait = self.max_cooldown_s
            elif self._failures < self.failures_to_open:
                return
            self._opened_at = self._clock()
            self._probing = False
        if not was_open:
            self._notify("open")

    def _notify(self, state: str) -> None:
        if self._on_change is not None:
            self._on_change(state)
