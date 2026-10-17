"""Reproduce un fixture o el log de una corrida por `alerts.process()` (MAT-6).

Es el plan B de la demo: sin video ni modelo, las observaciones salen del archivo y se
respetan los tiempos de sus `ts`, divididos por `--speed`. Acepta las dos formas de
03-contratos.md: `fixtures/observations/*.jsonl` (una observación por línea) y
`out/eventos.jsonl` (se toman las líneas `"event": "observation"`, el resto se saltea).

El log de la reproducción va a `out/replay.jsonl` (no a `eventos.jsonl`, así reproducir
el log de una corrida no le agrega líneas al mismo archivo).

Uso (desde vision/):
  python tools/replay.py --file ../fixtures/observations/mesa_completa.jsonl --speed 10
  python tools/replay.py --file out/eventos.jsonl --demo --no-telegram
"""

import argparse
import json
import logging
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from servio_vision import alerts
from servio_vision.alerts.config import DEFAULT_INTERVAL, load_config
from servio_vision.alerts.models import TableObservation

logger = logging.getLogger(__name__)

DEFAULT_LOG = alerts.VISION_DIR / "out" / "replay.jsonl"


def read_observations(path: Path) -> list[TableObservation]:
    """Lee todas las observaciones del archivo (fixture o log de eventos)."""
    observations = []
    for number, line in enumerate(path.read_text("utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            data = json.loads(line)
            event = data.pop("event", "observation")
            if event == "observation":
                observations.append(TableObservation.model_validate(data))
        except ValueError as error:
            raise ValueError(f"{path}:{number}: línea inválida: {error}") from None
    return observations


def replay(
    observations: Sequence[TableObservation],
    process: Callable[[TableObservation], list[dict[str, Any]]],
    speed: float,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Pasa cada observación por `process` esperando entre `ts`; devuelve los avisos.

    Si el `ts` va hacia atrás (un log con varias corridas) no se espera, y si `process`
    rechaza la observación se avisa y se sigue, como en `main.py`.
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
    parser.add_argument("--demo", action="store_true", help="umbrales de demo")
    parser.add_argument(
        "--interval",
        type=int,
        default=DEFAULT_INTERVAL,
        help="segundos entre lecturas de la corrida (para los umbrales)",
    )
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG, help="log de eventos")
    args = parser.parse_args()
    # La consola de Windows (cp1252) no imprime los emojis de los avisos.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    try:
        observations = read_observations(args.file)
        config = load_config(args.interval, args.demo)
    except (OSError, ValueError) as error:
        print(error, file=sys.stderr)
        return 1
    if not observations:
        print(f"{args.file}: no hay observaciones", file=sys.stderr)
        return 1

    span = (observations[-1].ts - observations[0].ts).total_seconds()
    print(
        f"{len(observations)} observaciones, {span:g} s de video "
        f"→ ~{span / args.speed:g} s a {args.speed:g}×"
    )
    alerts.configure(config, telegram=not args.no_telegram, log_path=args.log)
    try:
        count = replay(observations, alerts.process, args.speed)
    except KeyboardInterrupt:
        print("cortado", file=sys.stderr)
        return 130
    print(f"listo: {count} eventos de aviso, log en {args.log}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
