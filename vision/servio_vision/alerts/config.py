"""Umbrales de los avisos (docs/context/05-reglas-alertas.md, «Umbrales configurables»).

Todos cuentan tiempo de video (el `ts` de las observaciones), en segundos.
Prioridad: flag de CLI > variable de entorno (`ALERT_T1`, `ALERT_T2`, `ALERT_REMINDER`,
`ALERT_MAX_REMINDERS`, `ALERT_DONE_WAIT`) > valor por defecto (producción, o
`2 × interval` con `--demo`; `max_reminders` es 2 en los dos casos).
"""

from collections.abc import Mapping
from dataclasses import dataclass

from servio_vision import settings

# Producción (05-reglas-alertas.md).
PROD_T1 = 60  # plato_vacio: plato en `vacio` hace ≥ T1
PROD_T2 = 10 * 60  # plato_lleno_mucho: plato en `lleno` hace ≥ T2
PROD_REMINDER = 3 * 60  # R: recordatorio si la condición sigue
MAX_REMINDERS = 2  # recordatorios antes de escalar (el escalado sale a R × (max + 1))
PROD_DONE_WAIT = 10 * 60  # «Listo» sin que la cámara lo vea resuelto: insiste y escala

# Segundos entre lecturas por defecto (`--interval` de main.py, 04-vision.md).
DEFAULT_INTERVAL = settings.DEFAULT_INTERVAL  # un solo default de cadencia

# El suavizado necesita 2 lecturas para aceptar un cambio: es lo mínimo observable.
MIN_READINGS = 2

ENV_VARS = {
    "t1": "ALERT_T1",
    "t2": "ALERT_T2",
    "reminder": "ALERT_REMINDER",
    "max_reminders": "ALERT_MAX_REMINDERS",
    "done_wait": "ALERT_DONE_WAIT",
}


@dataclass(frozen=True)
class AlertConfig:
    interval: int  # segundos entre lecturas del pipeline (--interval)
    t1: int
    t2: int
    reminder: int
    max_reminders: int = MAX_REMINDERS
    done_wait: int = PROD_DONE_WAIT

    def __post_init__(self) -> None:
        if self.interval <= 0:
            raise ValueError(f"interval tiene que ser > 0 (vino {self.interval})")
        if self.max_reminders < 0:
            raise ValueError(
                f"max_reminders tiene que ser >= 0 (vino {self.max_reminders})"
            )
        minimum = MIN_READINGS * self.interval
        too_short = [
            f"{name}={value}s"
            for name, value in (
                ("t1", self.t1),
                ("t2", self.t2),
                ("reminder", self.reminder),
                ("done_wait", self.done_wait),
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
    max_reminders: int | None = None,
    done_wait: int | None = None,
    env: Mapping[str, str] | None = None,
) -> AlertConfig:
    """Arma la config de avisos. `demo` ya resuelto por el llamador (--demo le gana a
    DEMO_MODE). Sin `env`, los umbrales salen de la configuración (settings.py)."""
    flags = {
        "t1": t1,
        "t2": t2,
        "reminder": reminder,
        "max_reminders": max_reminders,
        "done_wait": done_wait,
    }
    if env is None:
        cfg = settings.current()  # ya validados (enteros)
        configured = {
            "t1": cfg.alert_t1,
            "t2": cfg.alert_t2,
            "reminder": cfg.alert_reminder,
            "max_reminders": cfg.alert_max_reminders,
            "done_wait": cfg.alert_done_wait,
        }
    else:
        configured = {key: _from_env(env, ENV_VARS[key]) for key in flags}
    if demo:
        defaults = dict.fromkeys(flags, MIN_READINGS * interval)
    else:
        defaults = {
            "t1": PROD_T1,
            "t2": PROD_T2,
            "reminder": PROD_REMINDER,
            "done_wait": PROD_DONE_WAIT,
        }
    defaults["max_reminders"] = MAX_REMINDERS  # cuántos, no un tiempo: igual en demo

    values: dict[str, int] = {}
    for key, flag in flags.items():
        from_config = configured[key]
        if flag is not None:
            values[key] = flag
        elif from_config is not None:
            values[key] = from_config
        else:
            values[key] = defaults[key]
    return AlertConfig(interval=interval, **values)
