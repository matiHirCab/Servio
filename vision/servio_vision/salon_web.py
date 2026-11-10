"""Servidor de la vista del salón en vivo (F-1).

Sin dependencias nuevas: `http.server` de la biblioteca estándar, un hilo por
navegador. Rutas (`<local>` es el `local_id` de la casa, `local` por defecto):

- `/<local>/salon`          la página (static/salon.html)
- `/<local>/salon/estado`   la última foto del salón en JSON (`salon.py`)
- `/<local>/salon/eventos`  la misma foto por Server-Sent Events, cada vez que cambia
- `…?video=1`               (en las dos) la foto lleva además el frame de la cámara de
                            esa misma lectura, ya difuminado, en base64 (`frame_jpeg`;
                            o `frame_error` si no se pudo codificar): la página del
                            modo video recibe imagen y datos juntos. Solo con
                            `--web-video` (`SalonFeed(video=True)`); si no, se ignora
- `/<local>/salon/frame.jpg` el último frame, con la versión de su foto en
                            `X-Salon-Version`: para mirar la cámara con un `curl` o
                            el navegador al armarla. Solo con `--web-video`, si no
                            404; 503 si no se pudo codificar
- `/`                       redirige a `/<local>/salon`

Por defecto escucha solo en esta máquina (127.0.0.1). Con `--web-host 0.0.0.0` se
abre desde el celular o la tablet en la misma red. No tiene usuarios ni contraseña:
no la expongas a internet.
"""

import ipaddress
import logging
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlsplit

from servio_vision.salon import BROKEN_FRAME, SalonFeed

logger = logging.getLogger("servio")

PAGE = Path(__file__).with_name("static") / "salon.html"
KEEPALIVE_S = 15.0  # un comentario SSE cada tanto, para que no se corte la conexión


class SalonServer:
    """`start()` levanta el servidor en un hilo propio; `stop()` lo apaga."""

    def __init__(self, feed: SalonFeed, host: str = "127.0.0.1", port: int = 8000):
        self.feed = feed
        self.closing = threading.Event()
        self.httpd = ThreadingHTTPServer((host, port), _handler(self))
        self.httpd.daemon_threads = True
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        return self.httpd.server_address[1]

    @property
    def host(self) -> str:
        return str(self.httpd.server_address[0])

    @property
    def exposed(self) -> bool:
        """Si escucha para otros equipos de la red (no solo esta máquina)."""
        return not _is_loopback(self.host)

    @property
    def url(self) -> str:
        """La dirección para abrirla desde esta máquina."""
        return self._url("localhost" if self.host in ("0.0.0.0", "") else self.host)

    @property
    def reachable_url(self) -> str:
        """La dirección que usan los demás equipos de la red (con todas las
        interfaces no hay una sola: la IP de esta máquina)."""
        return self._url(
            "<IP de esta PC>" if self.host in ("0.0.0.0", "") else self.host
        )

    def _url(self, host: str) -> str:
        return f"http://{host}:{self.port}/{quote(self.feed.local_id)}/salon"

    def start(self) -> "SalonServer":
        self._thread = threading.Thread(
            target=self.httpd.serve_forever, name="salon-web", daemon=True
        )
        self._thread.start()
        return self

    def stop(self) -> None:
        self.closing.set()
        self.httpd.shutdown()
        self.httpd.server_close()


def _is_loopback(host: str) -> bool:
    """Una dirección que solo ve esta máquina. `host` es la ya ligada
    (`server_address`, siempre numérica); un valor raro cuenta como expuesto."""
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _handler(server: SalonServer) -> type[BaseHTTPRequestHandler]:
    feed = server.feed

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, format, *args):
            logger.debug("web: " + format, *args)

        def do_GET(self) -> None:
            url = urlsplit(self.path)
            parts = [unquote(p) for p in url.path.split("/") if p]
            if not parts:
                return self._redirect(f"/{quote(feed.local_id)}/salon")
            if parts[0] != feed.local_id or len(parts) < 2 or parts[1] != "salon":
                return self._send(HTTPStatus.NOT_FOUND, b"No existe", "text/plain")
            rest = parts[2:]
            if not rest:
                return self._send(
                    HTTPStatus.OK, PAGE.read_bytes(), "text/html; charset=utf-8"
                )
            # el frame con la foto: solo si el modo video está prendido (si no, la foto
            # de siempre, y una sola en la caché)
            with_video = feed.video and parse_qs(url.query).get("video", [""])[0] == "1"
            if rest == ["estado"]:
                body = feed.payload(feed.published(), with_video)
                return self._send(HTTPStatus.OK, body, "application/json")
            if rest == ["eventos"]:
                return self._events(with_video)
            if rest == ["frame.jpg"]:
                return self._frame()
            return self._send(HTTPStatus.NOT_FOUND, b"No existe", "text/plain")

        def _frame(self) -> None:
            if not feed.video:
                return self._send(
                    HTTPStatus.NOT_FOUND,
                    "El modo video está apagado (--web-video)".encode(),
                    "text/plain; charset=utf-8",
                )
            found = feed.frame_jpeg()
            if found is None:
                return self._send(
                    HTTPStatus.NOT_FOUND,
                    "Todavía no hay frame".encode(),
                    "text/plain; charset=utf-8",
                )
            if found.jpeg is None:
                return self._send(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    BROKEN_FRAME.encode(),
                    "text/plain; charset=utf-8",
                )
            self._send(
                HTTPStatus.OK,
                found.jpeg,
                "image/jpeg",
                {"X-Salon-Version": str(found.version)},
            )

        def _redirect(self, location: str) -> None:
            self.send_response(HTTPStatus.FOUND)
            self.send_header("Location", location)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _send(
            self,
            status: HTTPStatus,
            body: bytes,
            content_type: str,
            headers: dict[str, str] | None = None,
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            try:
                self.end_headers()
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass  # el navegador cortó (una bajada de frame.jpg que ya no quería)

        def _events(self, with_video: bool) -> None:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            version = -1  # la primera vez manda lo que haya
            try:
                while not server.closing.is_set():
                    published = feed.wait_published(version, KEEPALIVE_S)
                    if published.version != version and published.snapshot is not None:
                        data = feed.payload(published, with_video)
                        self.wfile.write(b"data: " + data + b"\n\n")
                    else:
                        self.wfile.write(b": sigo\n\n")
                    version = published.version
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass  # el navegador cerró la pestaña

    return Handler
