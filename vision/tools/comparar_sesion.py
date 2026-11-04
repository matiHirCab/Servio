"""¿La reproducción de una sesión dio los mismos avisos que la corrida en vivo? (B-1)

Compara los avisos grabados en `DIR/eventos.jsonl` con los de la última corrida del
log de `alerts` (`out/eventos.jsonl`, la reproducción recién hecha). Un aviso es
(evento, tipo, mesa, ts); coinciden si la lista es la misma.

    python main.py --source out/sesiones/cena-jueves --classifier hybrid --no-telegram
    python tools/comparar_sesion.py out/sesiones/cena-jueves

Sale con 0 si coinciden, 1 si no, 2 si falta algo.
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from servio_vision import recording
from servio_vision.alerts import DEFAULT_LOG


def compare(recorded: list[str], replayed: list[str]) -> list[str]:
    """Las diferencias, en palabras; vacío si los avisos coinciden."""
    a, b = recording.alert_signature(recorded), recording.alert_signature(replayed)
    if a == b:
        return []
    found = []
    for event in [x for x in a if x not in b]:
        found.append(f"grabado y no reproducido: {' · '.join(event)}")
    for event in [x for x in b if x not in a]:
        found.append(f"reproducido y no grabado: {' · '.join(event)}")
    if not found:  # mismos avisos en otro orden
        found.append("los mismos avisos, en otro orden")
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("session", type=Path, help="carpeta de la sesión grabada")
    parser.add_argument(
        "--log", type=Path, default=DEFAULT_LOG, help="log de la reproducción"
    )
    args = parser.parse_args(argv)
    events = args.session / recording.EVENTS_FILE
    if not events.exists():
        print(f"No existe {events}: ¿la sesión se grabó con --record?", file=sys.stderr)
        return 2
    if not args.log.exists():
        print(f"No existe {args.log}: ¿ya reprodujiste la sesión?", file=sys.stderr)
        return 2
    recorded = [line for line in events.read_text("utf-8").splitlines() if line.strip()]
    replayed = recording.last_run_lines(args.log)
    diffs = compare(recorded, replayed)
    total = len(recording.alert_signature(recorded))
    if not diffs:
        print(f"Coinciden: {total} eventos de aviso iguales en vivo y reproducidos.")
        return 0
    print(f"No coinciden ({len(diffs)} diferencias sobre {total} avisos grabados):")
    for d in diffs:
        print(" -", d)
    return 1


if __name__ == "__main__":
    sys.exit(main())
