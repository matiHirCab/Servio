"""Latido del servicio: avisa por un canal aparte cuando dejan de llegar lecturas.

Si la cámara se cae, el wifi se corta o el loop se cuelga, los mozos no reciben
avisos y nadie se entera. `HealthMonitor` mira cuándo fue la última lectura
procesada (`beat()`, que llama el loop) y, si pasan más de `stall_s`, manda un
mensaje al canal de quien mantiene Servio (no al grupo de mozos). Cuando las
lecturas vuelven, avisa de nuevo. Un hilo propio hace la revisión: el loop puede estar
justo trabado esperando a la cámara.
"""

import logging
import threading
import time
from collections.abc import Callable

logger = logging.getLogger(__name__)

STALL_S = 300.0  # sin lecturas por tanto tiempo = algo anda mal
CHECK_S = 15.0  # cada cuánto mira el hilo


class HealthMonitor:
    def __init__(
        self,
        send: Callable[[str], bool],
        stall_s: float = STALL_S,
        check_s: float = CHECK_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """`send(texto)`: manda al canal de salud; devuelve si llegó."""
        self._send = send
        self.stall_s = stall_s
        self.check_s = check_s
        self._clock = clock
        self._last_beat = clock()
        self._alerted = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def beat(self) -> None:
        """Llegó (y se procesó) una lectura."""
        self._last_beat = self._clock()

    def check(self) -> None:
        """Avisa de la caída (una vez) o de la recuperación. Nunca lanza."""
        stalled_for = self._clock() - self._last_beat
        if stalled_for > self.stall_s and not self._alerted:
            minutes = max(1, round(stalled_for / 60))
            text = (
                f"⚠️ Servio: hace {minutes} min que no llegan lecturas "
                "(¿cámara, internet o el programa trabado?). Los mozos no están "
                "recibiendo avisos."
            )
            # si el canal falló no se da por avisado: se reintenta en la próxima
            self._alerted = self._deliver(text)
        elif stalled_for <= self.stall_s and self._alerted:
            if self._deliver("✅ Servio volvió a recibir lecturas."):
                self._alerted = False

    def _deliver(self, text: str) -> bool:
        logger.warning(text)
        try:
            return bool(self._send(text))
        except Exception:
            logger.exception("No se pudo mandar el aviso de salud")
            return False

    def start(self) -> None:
        self._last_beat = self._clock()  # el arranque (cargar modelos) no cuenta
        self._thread = threading.Thread(target=self._run, name="health", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)

    def _run(self) -> None:
        while not self._stop.wait(self.check_s):
            self.check()
