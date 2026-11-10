"""Etiquetar recortes de platos de una sesión grabada (B-3, parte 1).

Dos pasos, los dos desde `vision/`:

1. **Recortar.** Recorre los frames de una sesión grabada (B-1) y guarda un JPEG por
   plato en una carpeta del set de evaluación. Las mesas salen de las zonas con que
   se grabó la sesión (`sesion.json`); los platos, del detector de YOLO si está
   instalado (`requirements-yolo.txt`). Sin detector, o si la sesión detectaba las
   mesas solas, se guarda la mesa entera (o el frame entero) como un recorte, y se
   etiqueta igual: una persona sabe si ese plato está lleno aunque la caja sea grande.

       python tools/etiquetar.py recortar out/sesiones/cena-jueves --set data/eval/cena --cada 3

2. **Etiquetar.** Una página local recorre los recortes que todavía no tienen
   etiqueta y deja marcar el estado real (`lleno`, `comiendo`, `por_terminar`,
   `vacio`) y el tipo (plato de comida, pan o guarnición, otro), con el teclado. Cada
   respuesta se escribe enseguida en `labels.csv` (`archivo,estado,tipo,nota`), el
   mismo que lee `tools/eval.py`: se puede cortar y seguir después.

       python tools/etiquetar.py data/eval/cena --web 8766

Las imágenes no van al repo (09-convenciones.md): `data/` y `out/` están ignorados.
Etiquetar de verdad los 300 recortes de 3 casas es B-3h (una persona).
"""

import argparse
import csv
import json
import logging
import sys
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Protocol, get_args
from urllib.parse import unquote, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cv2
import numpy as np

from servio_vision import recording, utf8_console
from servio_vision.schemas import PlateState
from servio_vision.zones import crop_table, to_jpeg

logger = logging.getLogger("servio")

STATES: tuple[str, ...] = get_args(PlateState)  # los del contrato, en su orden
SKIP = (
    "saltado"  # no es un plato (o no se ve): queda anotado para no volver a mostrarlo
)
KINDS = ("plato", "guarnicion", "otro")  # plato de comida · pan o guarnición · otro
LABELS_FILE = "labels.csv"
# lo que puede haber en un set: los recortes propios son JPEG, los de afuera
# (Nutrition5k, Roboflow) también PNG
IMAGE_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}
COLUMNS = ("archivo", "estado", "tipo", "nota")
JPEG_QUALITY = 90
PAD = 0.08  # aire alrededor de la caja del plato (fracción del lado)


class Detector(Protocol):
    def detect(
        self, image: np.ndarray
    ) -> list[Any]: ...  # cada uno con `.box` x1,y1,x2,y2


# ---------------------------------------------------------------- recortar


def load_detector() -> Detector | None:
    """El detector de platos de YOLO, o `None` si no está instalado."""
    try:
        from servio_vision.detector import PlateDetector

        return PlateDetector()
    except ImportError:
        logger.warning(
            "Sin ultralytics (requirements-yolo.txt): se guarda la mesa entera como "
            "recorte, no cada plato"
        )
        return None


def _padded(box, width: int, height: int) -> tuple[int, int, int, int]:
    x1, y1, x2, y2 = box
    pad = PAD * max(x2 - x1, y2 - y1)
    return (
        max(0, int(x1 - pad)),
        max(0, int(y1 - pad)),
        min(width, int(x2 + pad) + 1),
        min(height, int(y2 + pad) + 1),
    )


def crop_session(
    session: Path,
    out: Path,
    every: int = 1,
    detector: Detector | None = None,
) -> list[Path]:
    """Guarda en `out` un JPEG por plato (o por mesa, sin detector) de cada `every`
    frames de la sesión. Devuelve los archivos nuevos; los que ya estaban no se
    vuelven a escribir."""
    if every < 1:
        raise ValueError("--cada tiene que ser 1 o más")
    zones = recording.session_zones(session)
    frames = recording.session_frames(session)[::every]
    out.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for path, ts in frames:
        # como recording.py: cv2.imread/imwrite no pueden con acentos en Windows
        frame = cv2.imdecode(np.fromfile(path, np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            logger.warning("%s no se pudo leer, lo salteo", path.name)
            continue
        stamp = ts.strftime("%Y%m%dT%H%M%S")
        if zones is None:
            regions = [("frame", frame)]
        else:
            regions = [(z.table_id, crop_table(frame, z, zones)) for z in zones.tables]
        for table_id, crop in regions:
            if crop.size == 0:
                continue
            if detector is None:
                written += _save(out, f"{stamp}_{table_id}.jpg", crop)
                continue
            found = detector.detect(cv2.cvtColor(crop, cv2.COLOR_BGR2RGB))
            for n, det in enumerate(found, 1):
                bx0, by0, bx1, by1 = _padded(det.box, crop.shape[1], crop.shape[0])
                plate = crop[by0:by1, bx0:bx1]
                if plate.size:
                    written += _save(out, f"{stamp}_{table_id}_{n}.jpg", plate)
    logger.info("%d recortes nuevos en %s", len(written), out)
    return written


def _save(out: Path, name: str, image: np.ndarray) -> list[Path]:
    target = out / name
    if target.exists():
        return []
    try:
        target.write_bytes(to_jpeg(image, JPEG_QUALITY))
    except ValueError as e:
        raise OSError(f"No se pudo escribir {target}: {e}") from e
    return [target]


# ---------------------------------------------------------------- etiquetas


def read_labels(folder: Path) -> dict[str, dict[str, str]]:
    """Las etiquetas ya puestas, por archivo (la última si se repitió)."""
    path = folder / LABELS_FILE
    if not path.exists():
        return {}
    with open(path, encoding="utf-8", newline="") as f:
        return {row["archivo"]: row for row in csv.DictReader(f) if row.get("archivo")}


def is_image(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in IMAGE_TYPES


def crop_in(folder: Path, name: str) -> Path | None:
    """El recorte `name` de la carpeta, o `None` si no es un nombre de archivo de
    ahí adentro (nada de `..`, carpetas ni `C:foo.jpg`) o no es una imagen."""
    target = folder / name
    if Path(name).name != name or target.resolve().parent != folder.resolve():
        return None
    return target if is_image(target) else None


def pending(folder: Path) -> list[str]:
    """Los recortes de la carpeta sin etiqueta, en orden."""
    done = read_labels(folder)
    return sorted(
        p.name for p in folder.iterdir() if is_image(p) and p.name not in done
    )


def write_label(folder: Path, archivo: str, estado: str, tipo: str, nota: str) -> None:
    """Agrega una fila a `labels.csv` (con encabezado si es nuevo). `ValueError` si
    el archivo no es un recorte de la carpeta o la etiqueta no es una de las
    conocidas."""
    if estado not in STATES and estado != SKIP:
        raise ValueError(f"estado desconocido: {estado!r}")
    if tipo not in KINDS:
        raise ValueError(f"tipo desconocido: {tipo!r}")
    if crop_in(folder, archivo) is None:
        raise ValueError(f"no es un recorte de la carpeta: {archivo!r}")
    path = folder / LABELS_FILE
    # el encabezado va si el archivo no existe o está vacío; y si la última línea
    # quedó sin salto (una edición a mano), la fila nueva no se le pega
    existing = path.read_bytes() if path.exists() else b""
    with open(path, "a", encoding="utf-8", newline="") as f:
        if existing and not existing.endswith(b"\n"):
            f.write("\r\n")
        writer = csv.writer(f)
        if not existing:
            writer.writerow(COLUMNS)
        writer.writerow([archivo, estado, tipo, nota.replace("\n", " ").strip()])


# ---------------------------------------------------------------- la página

PAGE = """<!doctype html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Etiquetar recortes</title>
<style>
:root{color-scheme:dark}
body{background:#07080a;color:#eef1f4;font-family:ui-sans-serif,system-ui,sans-serif;margin:0;padding:20px;display:grid;gap:16px;max-width:900px;margin-inline:auto}
header{display:flex;justify-content:space-between;align-items:baseline;gap:12px;flex-wrap:wrap;color:#9aa1ab}
header b{color:#eef1f4;font-weight:500}
.stage{background:#121418;border:1px solid rgba(255,255,255,.08);border-radius:16px;padding:12px;display:grid;place-items:center;min-height:320px}
.stage img{max-width:100%;max-height:60vh;border-radius:8px}
.row{display:flex;gap:10px;flex-wrap:wrap}
button{border:1px solid rgba(255,255,255,.12);background:#1a1d22;color:#eef1f4;font:inherit;padding:12px 16px;border-radius:12px;cursor:pointer;min-height:48px}
button kbd{color:#9aa1ab;margin-right:6px;font-family:inherit}
button[aria-pressed=true]{outline:2px solid #3E8BFF}
.st-lleno{border-color:#3EE08F}.st-comiendo{border-color:#F2C94C}.st-por_terminar{border-color:#FF8A3D}.st-vacio{border-color:#FF4D61}
input{background:#1a1d22;color:#eef1f4;border:1px solid rgba(255,255,255,.12);border-radius:12px;padding:12px;font:inherit;flex:1;min-width:200px}
.done{color:#3EE08F;font-size:20px;text-align:center;padding:40px}
.help{color:#5f6670;font-size:13px}
</style></head><body>
<header><div><b id="name">Cargando…</b></div><div id="progress"></div></header>
<div class="stage"><img id="img" alt="Recorte del plato" hidden><div id="done" class="done" hidden>No queda nada por etiquetar en esta carpeta.</div></div>
<div class="row" id="kinds">
  <button type="button" data-kind="plato" aria-pressed="true"><kbd>Q</kbd>Plato de comida</button>
  <button type="button" data-kind="guarnicion" aria-pressed="false"><kbd>W</kbd>Pan o guarnición</button>
  <button type="button" data-kind="otro" aria-pressed="false"><kbd>E</kbd>Otro</button>
</div>
<div class="row" id="states">
  <button type="button" class="st-lleno" data-state="lleno"><kbd>1</kbd>Lleno</button>
  <button type="button" class="st-comiendo" data-state="comiendo"><kbd>2</kbd>Comiendo</button>
  <button type="button" class="st-por_terminar" data-state="por_terminar"><kbd>3</kbd>Por terminar</button>
  <button type="button" class="st-vacio" data-state="vacio"><kbd>4</kbd>Vacío</button>
  <button type="button" data-state="saltado"><kbd>0</kbd>Saltar (no es un plato / no se ve)</button>
</div>
<div class="row"><input id="note" placeholder="Nota (opcional): manos encima, plato compartido, reflejo…"></div>
<p class="help">Primero el tipo (Q/W/E, queda elegido para los siguientes), después el estado (1–4) o Saltar (0). Cada respuesta se guarda enseguida en labels.csv.</p>
<script>
"use strict";
let current = null, kind = "plato";
const $ = id => document.getElementById(id);
async function next() {
  let d;
  try { const r = await fetch("siguiente"); if (!r.ok) throw new Error(await r.text()); d = await r.json(); }
  catch (e) { current = null; $("name").textContent = "Sin conexión con la herramienta (¿se cortó? recargá la página)"; return; }
  current = d.archivo;
  $("progress").textContent = `${d.etiquetados} etiquetados · ${d.pendientes} pendientes`;
  $("img").hidden = !current; $("done").hidden = !!current;
  $("name").textContent = current || "Listo";
  if (current) $("img").src = "img/" + encodeURIComponent(current);
  $("note").value = "";
}
function setKind(k) { kind = k; for (const b of document.querySelectorAll("#kinds button")) b.setAttribute("aria-pressed", String(b.dataset.kind === k)); }
async function label(state) {
  if (!current) return;  // nada que etiquetar, o una respuesta en camino: la tecla se ignora
  const archivo = current;
  current = null;
  try {
    const r = await fetch("etiqueta", {method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({archivo, estado: state, tipo: kind, nota: $("note").value})});
    if (!r.ok) throw new Error(await r.text());
  } catch (e) {  // el servidor se cortó, o rechazó la etiqueta: se avisa y se vuelve a intentar
    current = archivo;
    alert("No se guardó la etiqueta: " + e.message);
    return;
  }
  next();
}
document.querySelectorAll("#kinds button").forEach(b => b.onclick = () => setKind(b.dataset.kind));
document.querySelectorAll("#states button").forEach(b => b.onclick = () => label(b.dataset.state));
document.addEventListener("keydown", e => {
  if (e.ctrlKey || e.metaKey || e.altKey) return;  // Ctrl+2 cambia de pestaña, no etiqueta
  if (e.target === $("note")) return;
  const k = {q: "plato", w: "guarnicion", e: "otro"}[e.key.toLowerCase()];
  if (k) return setKind(k);
  const s = {"1": "lleno", "2": "comiendo", "3": "por_terminar", "4": "vacio", "0": "saltado"}[e.key];
  if (s) label(s);
});
next();
</script></body></html>
"""


class LabelServer:
    """La página de etiquetar, en un hilo propio: `/` (página), `/siguiente` (el
    próximo recorte sin etiqueta, JSON), `/img/<archivo>` y `POST /etiqueta`."""

    def __init__(self, folder: Path, host: str = "127.0.0.1", port: int = 8766):
        self.folder = folder
        self.httpd = ThreadingHTTPServer((host, port), _handler(folder))
        self.httpd.daemon_threads = True

    @property
    def url(self) -> str:
        host, port = self.httpd.server_address[0], self.httpd.server_address[1]
        return f"http://{host}:{port}/"

    def start(self) -> "LabelServer":
        threading.Thread(
            target=self.httpd.serve_forever, name="etiquetar-web", daemon=True
        ).start()
        return self

    def serve(self) -> None:
        """Atiende en este hilo hasta Ctrl+C (un `Event().wait()` sin plazo no se
        corta con Ctrl+C en Windows)."""
        try:
            self.httpd.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            self.httpd.server_close()

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


def _handler(folder: Path) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, format, *args):
            logger.debug("etiquetar: " + format, *args)

        def do_GET(self) -> None:
            parts = [unquote(p) for p in urlsplit(self.path).path.split("/") if p]
            if not parts:
                return self._send(
                    HTTPStatus.OK, PAGE.encode(), "text/html; charset=utf-8"
                )
            if parts == ["siguiente"]:
                todo = pending(folder)
                body = {
                    "archivo": todo[0] if todo else None,
                    "pendientes": len(todo),
                    "etiquetados": len(read_labels(folder)),
                }
                return self._send(
                    HTTPStatus.OK,
                    json.dumps(body, ensure_ascii=False).encode(),
                    "application/json",
                )
            if len(parts) == 2 and parts[0] == "img":
                target = crop_in(folder, parts[1])
                if target is None:
                    return self._send(HTTPStatus.NOT_FOUND, b"No existe", "text/plain")
                return self._send(
                    HTTPStatus.OK,
                    target.read_bytes(),
                    IMAGE_TYPES[target.suffix.lower()],
                )
            self._send(HTTPStatus.NOT_FOUND, b"No existe", "text/plain")

        def do_POST(self) -> None:
            parts = [p for p in urlsplit(self.path).path.split("/") if p]
            try:
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(
                    length
                )  # siempre: con keep-alive, un cuerpo sin leer
                # se tomaría como el pedido siguiente
                if parts != ["etiqueta"]:
                    return self._send(HTTPStatus.NOT_FOUND, b"No existe", "text/plain")
                data = json.loads(body or b"{}")
                if not isinstance(data, dict):
                    return self._send(
                        HTTPStatus.BAD_REQUEST,
                        b"el cuerpo tiene que ser un objeto JSON",
                        "text/plain; charset=utf-8",
                    )
                write_label(
                    folder,
                    str(data.get("archivo", "")),
                    str(data.get("estado", "")),
                    str(data.get("tipo", "")),
                    str(data.get("nota", "")),
                )
            except (ValueError, OSError) as e:
                return self._send(
                    HTTPStatus.BAD_REQUEST, str(e).encode(), "text/plain; charset=utf-8"
                )
            self._send(HTTPStatus.OK, b"{}", "application/json")

        def _send(self, status: HTTPStatus, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

    return Handler


# ---------------------------------------------------------------- línea de comandos


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="cmd")
    cut = sub.add_parser("recortar", help="guardar los recortes de una sesión grabada")
    cut.add_argument("sesion", type=Path, help="carpeta de la sesión (B-1)")
    cut.add_argument(
        "--set", type=Path, required=True, help="carpeta del set (se crea)"
    )
    cut.add_argument("--cada", type=int, default=1, help="un frame de cada N")
    cut.add_argument(
        "--sin-detector", action="store_true", help="mesa entera, aunque haya YOLO"
    )
    tag = sub.add_parser("etiquetar", help="abrir la página para etiquetar un set")
    tag.add_argument("set", type=Path, help="carpeta con los recortes")
    tag.add_argument("--web", type=int, default=8766, metavar="PUERTO")
    tag.add_argument("--web-host", default="127.0.0.1")
    # `python tools/etiquetar.py data/eval/cena`: etiquetar, sin escribir el subcomando
    if argv is None:
        argv = sys.argv[1:]
    if argv and argv[0] not in ("recortar", "etiquetar", "-h", "--help"):
        argv = ["etiquetar", *argv]
    args = parser.parse_args(argv)
    utf8_console()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if args.cmd == "recortar":
        if not recording.is_session(args.sesion):
            parser.error(f"{args.sesion} no es una sesión grabada (falta sesion.json)")
        detector = None if args.sin_detector else load_detector()
        try:
            crop_session(args.sesion, args.set, args.cada, detector)
        except (OSError, ValueError) as e:
            parser.error(str(e))
        print(f"Para etiquetar: python tools/etiquetar.py {args.set.as_posix()}")
        return 0
    if args.cmd == "etiquetar":
        if not args.set.is_dir():
            parser.error(f"{args.set} no es una carpeta")
        server = LabelServer(args.set, args.web_host, args.web)
        print(
            f"Etiquetar en {server.url}  ({len(pending(args.set))} pendientes; Ctrl+C para cortar)"
        )
        server.serve()  # en el hilo principal: Ctrl+C corta también en Windows
        return 0
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
