"""Umbrales de los avisos (docs/context/05-reglas-alertas.md, «Umbrales configurables»).

Todos cuentan tiempo de video (el `ts` de las observaciones), en segundos.
Prioridad: flag de CLI > variable de entorno (`ALERT_T1`, `ALERT_T2`, `ALERT_REMINDER`)
> valor por defecto (producción, o `2 × interval` con `--demo`).
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass

# Producción (05-reglas-alertas.md).
PROD_T1 = 60  # plato_vacio: plato en `vacio` hace ≥ T1
PROD_T2 = 10 * 60  # plato_lleno_mucho: plato en `lleno` hace ≥ T2
PROD_REMINDER = 3 * 60  # R: recordatorio si la condición sigue
MAX_REMINDERS = 2

# El suavizado necesita 2 lecturas para aceptar un cambio: es lo mínimo observable.
MIN_READINGS = 2

ENV_VARS = {"t1": "ALERT_T1", "t2": "ALERT_T2", "reminder": "ALERT_REMINDER"}


@dataclass(frozen=True)
class AlertConfig:
    interval: int  # segundos entre lecturas del pipeline (--interval)
    t1: int
    t2: int
    reminder: int
    max_reminders: int = MAX_REMINDERS

    def __post_init__(self) -> None:
        if self.interval <= 0:
            raise ValueError(f"interval tiene que ser > 0 (vino {self.interval})")
        minimum = MIN_READINGS * self.interval
        too_short = [
            f"{name}={value}s"
            for name, value in (
                ("t1", self.t1),
                ("t2", self.t2),
                ("reminder", self.reminder),
            )
            if value < minimum
        ]
        if too_short:
            raise ValueError(
                f"umbrales por debajo de {MIN_READINGS} × interval = {minimum}s: "
                f"{', '.join(too_short)}. Subí el umbral o bajá --interval."
            )


def _from_env(env: Mapping[str, str], name: str) -> int | None:
    raw = env.get(name, "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        raise ValueError(
            f"{name} tiene que ser un entero en segundos (vino {raw!r})"
        ) from None


def load_config(
    interval: int,
    demo: bool,
    *,
    t1: int | None = None,
    t2: int | None = None,
    reminder: int | None = None,
    env: Mapping[str, str] | None = None,
) -> AlertConfig:
    """Arma la config de avisos. `demo` ya resuelto por el llamador (--demo le gana a DEMO_MODE)."""
    env = os.environ if env is None else env
    flags = {"t1": t1, "t2": t2, "reminder": reminder}
    if demo:
        defaults = dict.fromkeys(flags, MIN_READINGS * interval)
    else:
        defaults = {"t1": PROD_T1, "t2": PROD_T2, "reminder": PROD_REMINDER}

    values = {}
    for key, flag in flags.items():
        from_env = _from_env(env, ENV_VARS[key])
        if flag is not None:
            values[key] = flag
        elif from_env is not None:
            values[key] = from_env
        else:
            values[key] = defaults[key]
    return AlertConfig(interval=interval, **values)
