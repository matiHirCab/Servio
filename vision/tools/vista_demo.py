"""Vista de la demo: el video con lo que ve el sistema (JES-9).

Es la mitad izquierda de la pantalla dividida; la derecha es el celular con
el grupo de Telegram espejado (`scrcpy`). Corre el mismo pipeline que
`main.py` (mismos flags) y, encima del video, dibuja:
- el contorno de cada mesa con el color de su estado y un cartel con el
  estado y los minutos en ese estado;
- la caja de cada plato (`bbox`) con id, estado y % de comida.

El video se reproduce fluido; cada `--interval` segundos de video se manda
un frame al pipeline en segundo plano y la vista dibuja la última lectura.
Si el pipeline tarda más que la reproducción, el video espera y después
retoma su ritmo (lo mismo al salir de una pausa). Con la cámara, o con un
video y `--realtime`, el video nunca espera: si la IA está ocupada, esa
lectura se saltea y se toma la próxima apenas se libere (como en la demo en
vivo).

Fuentes: video, carpeta de fotos (cada foto dura `--interval` s), cámara o
URL (en vivo). A diferencia de `main.py`, sin `--speed` reproduce en tiempo
real, porque la vista es para mirar.

    python tools/vista_demo.py --source data/videos/demo.mp4 --classifier hybrid --demo
    python tools/vista_demo.py --source demo.mp4 --classifier fake --record out/demo.mp4

Teclas: q / Esc sale · espacio pausa.
"""

import logging
import sys
import time
from collections.abc import Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from main import Pipeline, build_parser, build_pipeline, setup_logging
from servio_vision import privacy
from servio_vision.alerts.state import table_status
from servio_vision.capture import (
    IMAGE_SUFFIXES,
    STREAM_PREFIXES,
    CameraReader,
    declared_fps,
    frame_seconds,
)
from servio_vision.schemas import Item
from servio_vision.zones import Zones, scaled_polygon, table_rect

logger = logging.getLogger("servio.vista")

WINDOW = "Servio"
MAX_PLAUSIBLE_FPS = 120
FALLBACK_FPS = 25
FACE_DETECT_EVERY = 3  # ~120 ms por detección en 1080p: cada 3 frames mantiene ~25 fps

# colores BGR, por estado de mesa y de plato
TABLE_COLORS = {
    "libre": (170, 170, 170),
    "servida": (230, 150, 40),
    "comiendo": (80, 190, 80),
    "por_terminar": (0, 200, 255),
    "terminada": (60, 60, 230),
}
PLATE_COLORS = {
    "lleno": (230, 150, 40),
    "comiendo": (80, 190, 80),
    "por_terminar": (0, 200, 255),
    "vacio": (60, 60, 230),
}
PLATE_LABELS = {
    "lleno": "lleno",
    "comiendo": "comiendo",
    "por_terminar": "por terminar",
    "vacio": "vacio",  # sin tilde: la fuente de OpenCV no tiene acentos
}
FONT = cv2.FONT_HERSHEY_SIMPLEX


@dataclass
class TableView:
    status: str = "libre"
    since: datetime | None = None  # desde cuándo está en este estado (tiempo de video)
    items: list[Item] = field(default_factory=list)


class DemoState:
    """Lo que se dibuja: la última lectura de cada mesa y desde cuándo está así."""

    def __init__(self, zones: Zones) -> None:
        self.zones = (
            zones  # mesas de la última lectura (con detección automática, cambian)
        )
        self.tables = {t.table_id: TableView() for t in zones.tables}
        self.readings = 0

    def update(self, pipeline: Pipeline, ts: datetime) -> None:
        self.readings += 1
        self.zones = getattr(pipeline, "zones", self.zones)
        for t in self.zones.tables:
            self.tables.setdefault(t.table_id, TableView())
        for table_id, view in self.tables.items():
            items = list(pipeline.previous.get(table_id, []))
            status = table_status(i.state for i in items if i.state)
            if status != view.status or view.since is None:
                view.status, view.since = status, ts
            view.items = items


def _label(img, text, org, color, scale=0.7, thickness=2) -> None:
    """Texto con fondo oscuro, para que se lea sobre cualquier imagen."""
    (w, h), base = cv2.getTextSize(text, FONT, scale, thickness)
    x, y = org
    cv2.rectangle(img, (x - 4, y - h - 6), (x + w + 4, y + base + 2), (20, 20, 20), -1)
    cv2.putText(img, text, (x, y), FONT, scale, color, thickness, cv2.LINE_AA)


def draw_overlay(
    frame: np.ndarray,
    zones: Zones,
    state: DemoState,
    now: datetime,
    header: str = "",
) -> np.ndarray:
    view = frame.copy()
    width = view.shape[1]
    scale = max(0.9, width / 1920)  # videos chicos (celular vertical): texto legible
    for zone in zones.tables:
        table = state.tables.get(zone.table_id, TableView())
        color = TABLE_COLORS[table.status]
        polygon = scaled_polygon(zone, zones, view)
        cv2.polylines(view, [polygon], True, color, max(2, round(4 * scale)))

        # cajas de los platos: bbox normalizada dentro del recorte de la mesa
        # (el mismo rectángulo que recibe el clasificador)
        try:
            x0, y0, x1, y1 = table_rect(view, zone, zones)
        except ValueError:
            continue  # mesa fuera del frame
        w, h = x1 - x0, y1 - y0
        for item in table.items:
            if item.bbox is None or item.state is None:
                continue
            bx, by, bw, bh = item.bbox
            p1 = (round(x0 + bx * w), round(y0 + by * h))
            p2 = (round(x0 + (bx + bw) * w), round(y0 + (by + bh) * h))
            plate_color = PLATE_COLORS[item.state]
            cv2.rectangle(view, p1, p2, plate_color, max(2, round(3 * scale)))
            # para el jurado, solo el estado (sin id, % ni confianza)
            text = PLATE_LABELS[item.state]
            _label(view, text, (p1[0], max(p1[1] - 8, 20)), plate_color, 0.8 * scale, 2)

        _label(
            view,
            zone.name,  # solo "Mesa 1": el estado se ve por el color
            (x0 + 6, y0 + round(22 * scale)),
            color,
            0.55 * scale,  # chico: no tapa la mesa
            1,
        )
    if header:
        _label(view, header, (12, round(36 * scale)), (255, 255, 255), 0.8 * scale, 2)
    return view


@dataclass
class ViewSource:
    """Frames para mostrar: `(frame, segundos de la fuente)`."""

    frames: Iterator[tuple[np.ndarray, float]]
    live: bool  # cámara o stream: tiempo real, sin esperar entre frames
    fps: float  # para grabar


def open_view_source(source: str, interval: float) -> ViewSource:
    """Abre la fuente con las mismas reglas que `capture.open_source`.

    A diferencia de `capture`, devuelve **todos** los frames (para que el video
    se vea fluido), no uno por intervalo. Una carpeta de fotos muestra cada foto
    durante `interval` segundos. Tira `OSError`/`FileNotFoundError` si no abre.
    """
    if source.isdigit() or source.startswith(STREAM_PREFIXES):
        # el mismo lector que main.py: descarta los frames negros del arranque,
        # se reconecta si el celular se corta y difumina las caras
        reader = CameraReader(
            int(source) if source.isdigit() else source,
            privacy.default_anonymizer(every=FACE_DETECT_EVERY),
        )
        reader.start()
        t0 = time.monotonic()
        frames = ((frame, time.monotonic() - t0) for frame in reader.frames())
        return ViewSource(frames, live=True, fps=reader.fps)
    path = Path(source)
    # como en capture.py: caras difuminadas apenas se lee, antes de mostrar o grabar
    if path.is_dir():
        files = sorted(p for p in path.iterdir() if p.suffix.lower() in IMAGE_SUFFIXES)
        if not files:
            raise FileNotFoundError(f"No hay imágenes en {path}")
        # fotos distintas: las caras de una no dicen nada de la siguiente, detectar
        # en todas
        anonymize = privacy.default_anonymizer()
        images = (cv2.imread(str(f)) for f in files)
        frames = (
            (anonymize(image), i * interval)
            for i, image in enumerate(images)
            if image is not None
        )
        return ViewSource(frames, live=False, fps=1 / interval)
    if path.is_file():
        cap = cv2.VideoCapture(str(path))
        if not cap.isOpened():
            raise OSError(f"No se pudo abrir el video: {path}")
        fps = declared_fps(cap)
        # frames seguidos: detecta cada FACE_DETECT_EVERY para que el video siga fluido
        anonymize = privacy.default_anonymizer(every=FACE_DETECT_EVERY)
        frames = (
            (anonymize(frame), frame_seconds(cap, i, fps))
            for i, frame in enumerate(_read_all(cap))
        )
        if fps > MAX_PLAUSIBLE_FPS:  # fps falsos (cámaras de seguridad): para grabar
            logger.warning("El video declara %g fps; grabo a %d", fps, FALLBACK_FPS)
            fps = FALLBACK_FPS
        return ViewSource(frames, live=False, fps=fps)
    raise FileNotFoundError(f"No existe la fuente: {source}")


def _read_all(cap: cv2.VideoCapture) -> Iterator[np.ndarray]:
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                return
            yield frame
    finally:
        cap.release()


def _screen_height() -> int:
    try:
        import ctypes

        return int(ctypes.windll.user32.GetSystemMetrics(1))  # alto de la pantalla
    except Exception:  # noqa: BLE001 — fuera de Windows, un alto razonable
        return 1000


def _fit_window(image: np.ndarray) -> None:
    """La ventana ocupa el alto de la pantalla, pegada a la izquierda: la otra mitad
    queda para Telegram."""
    height, width = image.shape[:2]
    target_h = int(_screen_height() * 0.88)
    target_w = int(width * target_h / height)
    cv2.resizeWindow(WINDOW, target_w, target_h)
    cv2.moveWindow(WINDOW, 0, 0)


def _wait(due: float | None, no_window: bool) -> str | None:
    """Espera hasta `due` atendiendo el teclado: "quit", "resumed" (tras pausa) o None."""
    if no_window:
        return None
    paused = False
    while True:
        if paused:
            delay = 50
        elif due is None:
            delay = 1
        else:
            delay = max(1, round((due - time.monotonic()) * 1000))
        key = cv2.waitKey(delay) & 0xFF
        closed = cv2.getWindowProperty(WINDOW, cv2.WND_PROP_VISIBLE) < 1
        if key in (ord("q"), 27) or closed:
            return "quit"
        if key == ord(" "):
            paused = not paused
            if not paused:
                return "resumed"
            continue
        if not paused and (due is None or time.monotonic() >= due):
            return None


class _Recorder:
    STILLS_FPS = 10  # una carpeta de fotos se graba a 10 fps repitiendo cada foto

    def __init__(self, path: Path | None, fps: float) -> None:
        self.path = path
        if fps < 1:  # carpeta de fotos: fps = 1 / interval
            self.fps = self.STILLS_FPS
            self.repeat = max(1, round(self.STILLS_FPS / fps))
        else:
            self.fps, self.repeat = fps, 1
        self.writer = None

    def write(self, image: np.ndarray) -> None:
        if self.path is None:
            return
        if self.writer is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            h, w = image.shape[:2]
            fourcc = cv2.VideoWriter_fourcc(*"mp4v")
            self.writer = cv2.VideoWriter(str(self.path), fourcc, self.fps, (w, h))
        for _ in range(self.repeat):
            self.writer.write(image)

    def close(self) -> None:
        if self.writer is not None:
            self.writer.release()
            logger.info("Video guardado en %s", self.path)


def run(args) -> int:
    try:
        source = open_view_source(args.source, args.interval)
    except (OSError, privacy.PrivacyError) as e:
        logger.error("%s", e)
        return 2
    pipeline = build_pipeline(args)
    if pipeline is None:
        return 2

    speed = args.speed or 1.0  # la vista es para mirar: por defecto, tiempo real
    # cámara (siempre) o video con --realtime: el video nunca espera a la IA
    realtime = source.live or args.realtime
    state = DemoState(pipeline.zones)
    worker = ThreadPoolExecutor(max_workers=1)
    recorder = _Recorder(args.record, source.fps)
    pending: Future | None = None
    pending_ts: datetime | None = None
    start = datetime.now(UTC)
    next_reading = 0.0
    submitted = 0
    # ritmo: el frame con `ref_s` segundos de fuente se mostró en `ref_clock`
    ref_clock, ref_s = time.monotonic(), 0.0
    last = None  # (frame, ts, video_s) del último frame mostrado
    interrupted = False
    window_fitted = False
    failed = False

    def finish_pending() -> None:
        nonlocal pending
        if pending is not None:
            future, pending = pending, None  # si falla, no se reintenta en el cierre
            future.result()
            state.update(pipeline, pending_ts)

    def render(frame, ts, video_s) -> np.ndarray:
        return draw_overlay(frame, state.zones, state, ts)

    if not args.no_window:
        cv2.namedWindow(WINDOW, cv2.WINDOW_NORMAL)
    try:
        for frame, video_s in source.frames:
            ts = (
                datetime.now(UTC) if source.live else start + timedelta(seconds=video_s)
            )
            if pending is not None and pending.done():
                finish_pending()
            limit_ok = args.limit is None or submitted < args.limit
            due_reading = video_s >= next_reading and limit_ok
            if due_reading and pending is not None and realtime:
                pass  # la IA sigue ocupada: el video no espera; se lee apenas se libere
            elif due_reading:
                if pending is not None:
                    # el pipeline va atrasado: el video espera y después retoma su ritmo
                    finish_pending()
                    ref_clock, ref_s = time.monotonic(), video_s
                pending = worker.submit(pipeline.process_frame, frame.copy(), ts)
                pending_ts = ts
                submitted += 1
                # en tiempo real, la próxima lectura cuenta desde ahora: si el modelo
                # se atrasó, no se acumulan lecturas pendientes
                base = video_s if realtime else next_reading
                next_reading = base + args.interval

            image = render(frame, ts, video_s)
            last = (frame, ts, video_s)
            recorder.write(image)
            if not args.no_window:
                cv2.imshow(WINDOW, image)
                if not window_fitted:
                    _fit_window(image)
                    window_fitted = True
            due = None if source.live else ref_clock + (video_s - ref_s) / speed
            if args.no_window and realtime and due is not None:
                # sin ventana pero en tiempo real: igual se respeta el reloj
                time.sleep(max(0.0, due - time.monotonic()))
            action = _wait(due, args.no_window)
            if action == "quit":
                break
            if action == "resumed":
                ref_clock, ref_s = time.monotonic(), video_s
            if args.limit is not None and submitted >= args.limit and pending is None:
                break
    except KeyboardInterrupt:
        interrupted = True
        logger.info("Cortado a mano")
    finally:
        if not interrupted:
            try:
                finish_pending()  # la última lectura también se muestra y se graba
                if last is not None:
                    recorder.write(render(*last))
            except Exception:
                logger.exception("Falló la última lectura")
                failed = True
        worker.shutdown(wait=not interrupted, cancel_futures=True)
        pipeline.close()
        recorder.close()
        cv2.destroyAllWindows()
    logger.info("%d lecturas", state.readings)
    return 0 if state.readings and not failed else 1


def main(argv: list[str] | None = None) -> int:
    parser = build_parser("Servio: vista de la demo.")
    parser.add_argument("--record", type=Path, help="guardar la vista en un video .mp4")
    parser.add_argument(
        "--realtime",
        action="store_true",
        help="video como cámara en vivo: no espera a la IA (salta lecturas si está ocupada)",
    )
    parser.add_argument(
        "--no-window", action="store_true", help="sin ventana (con --record)"
    )
    args = parser.parse_args(argv)
    setup_logging(args.verbose)
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
