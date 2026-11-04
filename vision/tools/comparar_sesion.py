"""¿La reproducción de una sesión dio los mismos avisos que la corrida en vivo? (B-1)

Compara los avisos grabados en `DIR/eventos.jsonl` con los de la reproducción
(`DIR/reproduccion.jsonl`, que deja `main.py --source DIR`; con `--log` se compara
contra la última corrida de otro log). Un aviso es (evento, tipo, mesa, ts);
coinciden si la lista es la misma.

    python main.py --source out/sesiones/cena-jueves --classifier hybrid --no-telegram
    python tools/comparar_sesion.py out/sesiones/cena-jueves

Sale con 0 si coinciden, 1 si no, 2 si falta algo.
"""

import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from servio_vision import recording


def compare(recorded: list[str], replayed: list[str]) -> list[str]:
    """Las diferencias, en palabras; vacío si los avisos coinciden."""
    a, b = recording.alert_signature(recorded), recording.alert_signature(replayed)
    if a == b:
        return []
    found = []
    ca, cb = Counter(a), Counter(b)  # con repetidos: uno de más también es diferencia
    for event, n in sorted((ca - cb).items()):
        found.extend([f"grabado y no reproducido: {' · '.join(event)}"] * n)
    for event, n in sorted((cb - ca).items()):
        found.extend([f"reproducido y no grabado: {' · '.join(event)}"] * n)
    if not found:  # mismos avisos en otro orden
        found.append("los mismos avisos, en otro orden")
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("session", type=Path, help="carpeta de la sesión grabada")
    parser.add_argument(
        "--log",
        type=Path,
        default=None,
        help="otro log de alerts: se toma su última corrida (sin esto, DIR/reproduccion.jsonl)",
    )
    args = parser.parse_args(argv)
    events = args.session / recording.EVENTS_FILE
    if not events.exists():
        print(f"No existe {events}: ¿la sesión se grabó con --record?", file=sys.stderr)
        return 2
    log = (
        args.log
        if args.log is not None
        else args.session / recording.REPLAY_EVENTS_FILE
    )
    if not log.exists():
        print(f"No existe {log}: ¿ya reprodujiste la sesión?", file=sys.stderr)
        return 2
    try:
        info = recording.read_info(args.session)
    except OSError as e:
        print(e, file=sys.stderr)
        return 2
    if info.get("truncated"):
        print(
            f"Ojo: la grabación se cortó a los {info.get('readings')} frames (error de"
            " disco); los avisos de después no se pueden reproducir."
        )
    if info.get("live"):
        print(
            "Nota: se grabó de una cámara en vivo; si al arrancar retomó avisos"
            " abiertos de la base, la reproducción (que arranca de cero) puede"
            " diferir al principio."
        )
    recorded = [line for line in events.read_text("utf-8").splitlines() if line.strip()]
    answers = sum('"event": "alert_feedback"' in line for line in recorded)
    if answers:
        print(
            f"Nota: en vivo los mozos respondieron {answers} veces; la reproducción no"
            " tiene respuestas, así que los avisos que se cerraron por una respuesta"
            " (o no recordaron) van a diferir."
        )
    replayed = recording.last_run_lines(log)
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
