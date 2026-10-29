"""Métricas del negocio: platos con problemas y mozos, para el cierre del pitch.

Todo sale de la base relacional (`vision/out/servio.db`, ver `servio_vision/db.py`)
con consultas SQL: lo que pasó en vivo (avisos, respuestas de los mozos) más el
historial simulado (`fixtures/restaurante/historial.jsonl`, que se importa la
primera vez). Muestra:
- platos que vuelven llenos: % de las veces que se sirvieron, con el motivo más
  común que respondieron los mozos;
- mozos: minutos hasta atender una mesa que terminó, y segundos en responder el aviso.

Con `--rol` muestra solo lo que ese rol puede ver según `config/servio.toml`
(ver `servio_vision/roles.py`): el mozo no ve métricas.

    python tools/metricas.py [--rol encargado] [--image out/metricas.png] [--sql]
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sqlalchemy

from servio_vision import settings
from servio_vision.db import DEFAULT_PATH, Database
from servio_vision.metrics import (
    HISTORY,
    REPORT_METRICS,
    SQL_ANSWER,
    SQL_ATTEND,
    SQL_DISHES,
    compute,
    print_report,
    render,
)
from servio_vision.restaurant import Restaurant
from servio_vision.roles import DEFAULT_CONFIG, Roles
from servio_vision.settings import Settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Métricas de platos y mozos.")
    parser.add_argument(
        "--db",
        type=Path,
        help=f"un SQLite puntual (por defecto {DEFAULT_PATH}, o DATABASE_URL si está)",
    )
    parser.add_argument("--historial", type=Path, default=HISTORY)
    parser.add_argument("--image", type=Path, help="guardar también como imagen")
    parser.add_argument("--sql", action="store_true", help="mostrar las consultas")
    parser.add_argument(
        "--rol", help="mostrar solo lo que ve este rol (mozo, encargado, dueno)"
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    visible = None
    if args.rol:
        role = Roles.load(args.config).get(args.rol)
        visible = set(role.visible_metrics()) & set(REPORT_METRICS)
        if not visible:
            print(f"El rol {role.nombre} no ve estas métricas (ver {args.config}).")
            return 3
    # la configuración de la casa (--config, el mismo toml de los roles) dice el
    # local_id; un --db explícito gana a DATABASE_URL (Postgres compartido)
    try:
        cfg = settings.activate(
            Settings.load(args.config if args.config.exists() else None)
        )
    except (OSError, ValueError) as e:
        print(f"Configuración: {settings.explain(e)}", file=sys.stderr)
        return 2
    url = None if args.db else cfg.database_url
    try:
        db = Database(args.db, url=url, local_id=cfg.local_id)
    except (RuntimeError, sqlalchemy.exc.OperationalError) as e:
        # falta el driver, o la URL no responde: una línea, sin traceback con la URL
        print(f"Base: {e}", file=sys.stderr)
        return 2
    if url:
        print(f"(base compartida: DATABASE_URL, local {cfg.local_id})\n")
    restaurant = Restaurant.load()
    db.load_restaurant(restaurant)
    if args.historial.exists():
        imported = db.import_history(args.historial, restaurant)
        if imported:
            print(f"(historial importado a la base: {imported} avisos)\n")
    if args.sql:
        for title, sql in (
            ("Platos que vuelven llenos", SQL_DISHES),
            ("Minutos hasta retirar los platos", SQL_ATTEND),
            ("Segundos en responder", SQL_ANSWER),
        ):
            print(f"-- {title}{db.render(sql)}")
    m = compute(db)
    db.close()
    if not m["dishes"]:
        print("La base no tiene datos: corré tools/generar_historial.py o una demo.")
        return 2
    print_report(m, visible)
    if args.image:
        if visible is not None and visible != set(REPORT_METRICS):
            print(
                "\n(la imagen muestra todas las métricas: no se guarda para este rol)"
            )
        else:
            render(m, args.image)
            print(f"\nImagen guardada en {args.image}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
