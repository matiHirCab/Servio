"""Métricas del negocio: platos con problemas y mozos, para el cierre del pitch.

Todo sale de la base relacional (`vision/out/servio.db`, ver `servio_vision/db.py`)
con consultas SQL: lo que pasó en vivo (avisos, respuestas de los mozos) más el
historial simulado (`fixtures/restaurante/historial.jsonl`, que se importa la
primera vez). Muestra:
- platos que vuelven llenos: % de las veces que se sirvieron, con el motivo más
  común que respondieron los mozos;
- mozos: minutos hasta atender una mesa que terminó, y segundos en responder el aviso.

    python tools/metricas.py [--image out/metricas.png] [--sql]
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from servio_vision.db import Database
from servio_vision.metrics import (
    HISTORY,
    SQL_ANSWER,
    SQL_ATTEND,
    SQL_DISHES,
    VISION_DIR,
    compute,
    print_report,
    render,
)
from servio_vision.restaurant import Restaurant

DB = VISION_DIR / "out" / "servio.db"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Métricas de platos y mozos.")
    parser.add_argument("--db", type=Path, default=DB)
    parser.add_argument("--historial", type=Path, default=HISTORY)
    parser.add_argument("--image", type=Path, help="guardar también como imagen")
    parser.add_argument("--sql", action="store_true", help="mostrar las consultas")
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    db = Database(args.db)
    restaurant = Restaurant.load()
    db.load_restaurant(restaurant)
    if args.historial.exists():
        imported = db.import_history(args.historial, restaurant)
        if imported:
            print(f"(historial importado a la base: {imported} avisos)\n")
    if args.sql:
        for title, sql in (
            ("Platos que vuelven llenos", SQL_DISHES),
            ("Minutos hasta atender", SQL_ATTEND),
            ("Segundos en responder", SQL_ANSWER),
        ):
            print(f"-- {title}{sql}")
    m = compute(db)
    db.close()
    if not m["dishes"]:
        print("La base no tiene datos: corré tools/generar_historial.py o una demo.")
        return 2
    print_report(m)
    if args.image:
        render(m, args.image)
        print(f"\nImagen guardada en {args.image}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
