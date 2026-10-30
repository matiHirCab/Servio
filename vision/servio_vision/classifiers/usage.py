"""Cuánto se usa el modelo: llamadas y tokens del día, y un tope de llamadas.

Cada lectura llama al modelo una vez por mesa: con 8 mesas a 30 s son ~960 llamadas
por hora, y antes no había forma de verlo (el uso salía solo en el nivel debug) ni de
ponerle un techo. `UsageMeter` lleva la cuenta del día (en UTC) y, con un tope,
`try_start` dice que no: el que llama sigue con lo de YOLO hasta mañana.
"""

import threading
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime


def _utc_today() -> date:
    return datetime.now(UTC).date()


class UsageMeter:
    def __init__(
        self,
        today: Callable[[], date] = _utc_today,
        report_every: int = 50,
    ) -> None:
        """`report_every`: cada cuántas llamadas toca loguear el resumen."""
        self._today = today
        self._report_every = report_every
        self._lock = threading.Lock()
        self._day = today()
        self._reset()

    def _reset(self) -> None:
        self.calls = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.cost_usd = 0.0
        self._cap_reported = False
        self._last_report = 0  # a cuántas llamadas se logueó el último resumen

    def _roll(self) -> None:
        if self._today() != self._day:  # otro día: se cuenta de nuevo
            self._day = self._today()
            self._reset()

    def try_start(self, cap: int | None) -> bool:
        """Anota una llamada; `False` (sin anotarla) si ya se llegó al tope del día."""
        with self._lock:
            self._roll()
            if cap is not None and self.calls >= cap:
                return False
            self.calls += 1
            return True

    def add_usage(self, usage: Mapping[str, float | None] | None) -> None:
        """Suma lo que informó el proveedor (cada uno trae distinto: openai solo la
        entrada, el CLI además el costo)."""
        with self._lock:
            self._roll()
            usage = usage or {}
            self.input_tokens += int(usage.get("input_tokens") or 0)
            self.output_tokens += int(usage.get("output_tokens") or 0)
            self.cost_usd += float(usage.get("cost_usd") or 0)

    def first_cap_report(self) -> bool:
        """`True` la primera vez del día que se pregunta: para avisar una sola vez."""
        with self._lock:
            self._roll()
            first = not self._cap_reported
            self._cap_reported = True
            return first

    def report_due(self) -> bool:
        """¿Toca loguear el resumen? Cada `report_every` llamadas."""
        with self._lock:
            due = (
                self.calls // self._report_every
                > self._last_report // self._report_every
            )
            if due:
                self._last_report = self.calls
            return due

    def summary(self) -> str:
        with self._lock:
            cost = f", costo informado ${self.cost_usd:.4f}" if self.cost_usd else ""
            return (
                f"hoy {self.calls} llamadas, {self.input_tokens} tokens de entrada, "
                f"{self.output_tokens} de salida{cost}"
            )
