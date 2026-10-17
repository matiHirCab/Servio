"""Reproduce un fixture o el log de una corrida por `alerts.process()` (MAT-6).

Es el plan B de la demo: sin video ni modelo, las observaciones salen del archivo y se
respetan los tiempos de sus `ts`, divididos por `--speed`. Acepta las dos formas de
03-contratos.md: `fixtures/observations/*.jsonl` (una observación por línea) y
`out/eventos.jsonl` (se toman las líneas `observation` y `run_started`, el resto se
saltea). Una línea inválida se avisa y se saltea.

`eventos.jsonl` se agrega corrida tras corrida, así que el archivo se corta en corridas
en cada `run_started`, que trae los umbrales con los que se hizo la corrida: el replay usa
esos. Sin `run_started` (fixtures, logs viejos) empieza otra corrida cuando el `ts` vuelve
atrás o salta más de `RUN_GAP_READINGS` lecturas, y los umbrales son los de producción
(`--demo`, `--interval` para cambiarlos; no se lee el entorno, como en los tests).
Por defecto se reproduce la última corrida; `--run N` elige otra y `--all` las pasa
todas, cada una con estado nuevo y sin esperar entre ellas.

El log de la reproducción va a `out/replay.jsonl` y se pisa en cada replay (nunca puede
ser el archivo que se reproduce ni `out/eventos.jsonl`).

Uso (desde vision/):
  python tools/replay.py --file ../fixtures/observations/mesa_completa.jsonl --speed 10
  python tools/replay.py --file out/eventos.jsonl --no-telegram
  python tools/replay.py --file out/eventos.jsonl --run 1
"""

import argparse
import json
import logging
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from servio_vision import alerts
from servio_vision.alerts.config import DEFAULT_INTERVAL, AlertConfig, load_config
from servio_vision.alerts.models import TableObservation
from servio_vision.alerts.processor import AlertProcessor

logger = logging.getLogger(__name__)

DEFAULT_LOG = alerts.VISION_DIR / "out" / "replay.jsonl"

# Sin `run_started`, un salto de más de estas lecturas sin observaciones es otra corrida.
RUN_GAP_READINGS = 10


@dataclass
class Run:
    """Una corrida: sus observaciones y, si vino de un `run_started`, sus umbrales."""

    observations: list[TableObservation] = field(default_factory=list)
    config: AlertConfig | None = None
    marked: bool = False  # empezó con `run_started`: no se corta por tiempo

    def seconds(self) -> float:
        return (self.observations[-1].ts - self.observations[0].ts).total_seconds()


def read_runs(path: Path, max_gap: float) -> list[Run]:
    """Lee el archivo y lo corta en corridas (ver el docstring del módulo)."""
    runs: list[Run] = []
    for number, line in enumerate(path.read_text("utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            data = json.loads(line)
            if not isinstance(data, dict):
                raise TypeError("no es un objeto JSON")
            event = data.pop("event", "observation")
            if event == "run_started":
                runs.append(Run(config=AlertConfig(**data["config"]), marked=True))
            elif event == "observation":
                obs = TableObservation.model_validate(data)
                if not runs or not _continues(runs[-1], obs, max_gap):
                    runs.append(Run())
                runs[-1].observations.append(obs)
        except (ValueError, TypeError, KeyError) as error:
            logger.warning("%s:%d: línea inválida, la salteo: %s", path, number, error)
    return [run for run in runs if run.observations]


def _continues(run: Run, obs: TableObservation, max_gap: float) -> bool:
    if run.marked or not run.observations:
        return True
    gap = (obs.ts - run.observations[-1].ts).total_seconds()
    return 0 <= gap <= max_gap


def replay(
    observations: Sequence[TableObservation],
    process: Callable[[TableObservation], list[dict[str, Any]]],
    speed: float,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Pasa cada observación por `process` esperando entre `ts`; devuelve los avisos.

    Es para una sola corrida (ver `read_runs`). Si `process` rechaza una observación se
    avisa y se sigue, como en `main.py`.
    """
    alert_events = 0
    previous = None
    for obs in observations:
        if previous is not None:
            wait = (obs.ts - previous).total_seconds() / speed
            if wait > 0:
                sleep(wait)
        previous = obs.ts
        try:
            events = process(obs)
        except ValueError as error:
            logger.warning(
                "%s %s: observación rechazada: %s", obs.ts, obs.table_id, error
            )
            continue
        alert_events += sum(e["event"] != "observation" for e in events)
    return alert_events


def positive(raw: str) -> float:
    value = float(raw)
    if value <= 0:
        raise argparse.ArgumentTypeError(f"tiene que ser > 0 (vino {raw})")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--file", type=Path, required=True, help="jsonl a reproducir")
    parser.add_argument(
        "--speed", type=positive, default=10.0, help="divide los tiempos (10 = 10×)"
    )
    parser.add_argument(
        "--no-telegram", action="store_true", help="imprimir en vez de mandar"
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="umbrales de demo (corridas sin run_started)",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=DEFAULT_INTERVAL,
        help="segundos entre lecturas (corridas sin run_started)",
    )
    which = parser.add_mutually_exclusive_group()
    which.add_argument(
        "--run",
        type=int,
        help="corrida a reproducir (1 = la primera; por defecto la última)",
    )
    which.add_argument("--all", action="store_true", help="todas las corridas")
    parser.add_argument(
        "--log", type=Path, default=DEFAULT_LOG, help="log de eventos (se pisa)"
    )
    args = parser.parse_args()
    # La consola de Windows (cp1252) no imprime los emojis de los avisos.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if args.log.resolve() in {args.file.resolve(), alerts.DEFAULT_LOG.resolve()}:
        print(
            f"--log {args.log}: no puede ser el archivo a reproducir ni el log de main.py",
            file=sys.stderr,
        )
        return 1
    try:
        fallback = load_config(args.interval, args.demo, env={})
        runs = read_runs(args.file, RUN_GAP_READINGS * args.interval)
    except (OSError, ValueError) as error:
        print(error, file=sys.stderr)
        return 1
    if not runs:
        print(f"{args.file}: no hay observaciones", file=sys.stderr)
        return 1

    if args.all:
        selected = runs
    elif args.run is None:
        selected = runs[-1:]
    elif 1 <= args.run <= len(runs):
        selected = [runs[args.run - 1]]
    else:
        print(
            f"--run {args.run}: el archivo tiene {len(runs)} corridas", file=sys.stderr
        )
        return 1
    span = sum(run.seconds() for run in selected)
    print(
        f"{len(runs)} corrida(s) en el archivo, reproduzco {len(selected)}: "
        f"{sum(len(run.observations) for run in selected)} observaciones, "
        f"{span:g} s de video → ~{span / args.speed:g} s a {args.speed:g}×"
    )

    args.log.unlink(missing_ok=True)
    count = 0
    processor: AlertProcessor | None = None
    try:
        for run in selected:
            config = run.config or fallback
            print(
                f"umbrales{' de la corrida' if run.config else ''}: t1={config.t1}s "
                f"t2={config.t2}s reminder={config.reminder}s"
            )
            # Estado nuevo por corrida: mesas y avisos de una no pasan a la otra.
            processor = alerts.configure(
                config, telegram=not args.no_telegram, log_path=args.log
            )
            count += replay(run.observations, processor.process, args.speed)
    except KeyboardInterrupt:
        print("cortado", file=sys.stderr)
        return 130
    finally:
        if processor is not None:
            processor.close()
    print(f"listo: {count} eventos de aviso, log en {args.log}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
