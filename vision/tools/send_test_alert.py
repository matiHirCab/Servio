"""Manda un aviso de prueba al grupo de mozos y después lo marca resuelto (MAT-4).

Lee `TELEGRAM_BOT_TOKEN` y `TELEGRAM_GROUP_CHAT_ID` del entorno o del `.env` de la
raíz del repo. Sale con código 1 si no se pudo mandar o editar.

Uso (desde vision/):
  python tools/send_test_alert.py
  python tools/send_test_alert.py --wait 5
  python tools/send_test_alert.py --no-telegram
"""

import argparse
import logging
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from servio_vision.alerts.notify import (
    ConsoleNotifier,
    TelegramNotifier,
    resolved_text,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
TEST_MESSAGE = "🍽️ Mesa 1 terminó: retirá los platos y ofrecé postre, café o la cuenta. (prueba de Servio)"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--wait", type=float, default=2.0, help="segundos antes de editar"
    )
    parser.add_argument(
        "--no-telegram", action="store_true", help="imprimir en vez de mandar"
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if args.no_telegram:
        notifier = ConsoleNotifier()
    else:
        load_dotenv(REPO_ROOT / ".env")
        try:
            notifier = TelegramNotifier.from_env()
        except ValueError as error:
            print(f"{error} (ver .env.example)", file=sys.stderr)
            return 1

    message_id = notifier.send(TEST_MESSAGE)
    if message_id is None:
        print("no se pudo mandar el aviso", file=sys.stderr)
        return 1
    print(f"mandado (message_id {message_id}); edito en {args.wait:g} s")
    time.sleep(args.wait)
    if not notifier.edit(message_id, resolved_text(TEST_MESSAGE)):
        print("no se pudo editar el aviso", file=sys.stderr)
        return 1
    print("editado: ✅ ya resuelto")
    return 0


if __name__ == "__main__":
    sys.exit(main())
