"""Ver el dashboard del encargado y del dueño (D-1) en el navegador.

Levanta un servidor HTTP mínimo con las rutas de `servio_vision/dashboard.py`:

    python tools/dashboard.py [--demo] [--port 8090]

y abrí http://localhost:8090/<local_id>/dashboard?rol=encargado (o ?rol=dueno).
`--demo` carga el historial simulado (fixtures/restaurante/historial.jsonl) en la
base, como `tools/metricas.py`, para ver números sin haber corrido la cámara.

Cuando el servidor del salón en vivo (`salon_web.py`) esté en develop, el
dashboard se monta ahí, en el mismo puerto, y esta herramienta queda para probar.
"""

import argparse
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sqlalchemy

from servio_vision import settings
from servio_vision.dashboard import Dashboard
from servio_vision.db import Database
from servio_vision.metrics import HISTORY
from servio_vision.restaurant import Restaurant
from servio_vision.roles import DEFAULT_CONFIG, Roles
from servio_vision.settings import Settings


def make_handler(dashboard: Dashboard) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # nombre que pide http.server
            url = urlsplit(self.path)
            if url.path in ("", "/"):
                self.send_response(302)
                self.send_header("Location", f"/{dashboard.db.local_id}/dashboard")
                self.end_headers()
                return
            res = dashboard.handle(url.path, url.query)
            if res is None:
                self.send_error(404)
                return
            self.send_response(res.status)
            self.send_header("Content-Type", res.content_type)
            self.send_header("Content-Length", str(len(res.body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(res.body)

        def log_message(self, fmt: str, *args) -> None:
            pass  # sin una línea por pedido

    return Handler


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Dashboard de encargado y dueño.")
    parser.add_argument("--db", type=Path, help="un SQLite puntual")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8090)
    parser.add_argument(
        "--demo", action="store_true", help="cargar el historial simulado"
    )
    args = parser.parse_args(argv)
    try:
        cfg = settings.activate(
            Settings.load(args.config if args.config.exists() else None)
        )
        roles = Roles.load(args.config)
    except (OSError, ValueError) as e:
        print(f"Configuración: {settings.explain(e)}", file=sys.stderr)
        return 2
    url = None if args.db else cfg.database_url
    try:
        db = Database(args.db, url=url, local_id=cfg.local_id)
    except (RuntimeError, sqlalchemy.exc.OperationalError) as e:
        print(f"Base: {e}", file=sys.stderr)
        return 2
    restaurant = Restaurant.load()
    db.load_restaurant(restaurant)
    if args.demo and HISTORY.exists():
        imported = db.import_history(HISTORY, restaurant)
        if imported:
            print(f"(historial importado a la base: {imported} avisos)")
    server = ThreadingHTTPServer(
        (args.host, args.port), make_handler(Dashboard(db, roles))
    )
    base = f"http://{args.host}:{args.port}/{cfg.local_id}/dashboard"
    print(f"Encargado: {base}?rol=encargado\nDueño:     {base}?rol=dueno")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
