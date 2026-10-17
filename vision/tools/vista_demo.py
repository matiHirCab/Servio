"""Vista de la demo: el video con lo que ve el sistema (JES-9).

Es la mitad izquierda de la pantalla dividida; la derecha es el celular con
el grupo de Telegram espejado (`scrcpy`). Corre el mismo pipeline que
`main.py` (mismos flags) y, encima del video, dibuja:
- el contorno de cada mesa con el color de su estado y un cartel con el
  estado y los minutos en ese estado;
- la caja de cada plato (`bbox`) con id, estado y % de comida.

El video se reproduce fluido; cada `--interval` segundos de video se manda
un frame al pipeline en segundo plano y la vista dibuja la última lectura.
Si el pipeline tarda más que la reproducción, el video espera.

    python tools/vista_demo.py --source data/videos/demo.mp4 --classifier hybrid --demo
    python tools/vista_demo.py --source demo.mp4 --classifier fake --record out/demo.mp4

Teclas: q / Esc sale · espacio pausa.
"""

import sys
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from main import Pipeline, build_parser, build_pipeline, logger, setup_logging
from servio_vision.alerts.state import table_status
from servio_vision.schemas import Item
from servio_vision.zones import Zones, scaled_polygon

WINDOW = "Servio"

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
TABLE_LABELS = {
    "libre": "libre",
    "servida": "servida",
    "comiendo": "comiendo",
    "por_terminar": "por terminar",
    "terminada": "terminada",
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
        self.tables = {t.table_id: TableView() for t in zones.tables}
        self.readings = 0

    def update(self, pipeline: Pipeline, ts: datetime) -> None:
        self.readings += 1
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
    frame: np.ndarray, zones: Zones, state: DemoState, now: datetime, header: str
) -> np.ndarray:
    view = frame.copy()
    height, width = view.shape[:2]
    scale = max(0.5, width / 1920)
    for zone in zones.tables:
        table = state.tables[zone.table_id]
        color = TABLE_COLORS[table.status]
        polygon = scaled_polygon(zone, zones, view)
        cv2.polylines(view, [polygon], True, color, max(2, round(4 * scale)))

        # cajas de los platos: bbox normalizada dentro del recorte de la mesa
        x0, y0, w, h = cv2.boundingRect(polygon)
        x0, y0 = max(x0, 0), max(y0, 0)
        w, h = min(w, width - x0), min(h, height - y0)
        for item in table.items:
            if item.bbox is None or item.state is None:
                continue
            bx, by, bw, bh = item.bbox
            p1 = (round(x0 + bx * w), round(y0 + by * h))
            p2 = (round(x0 + (bx + bw) * w), round(y0 + (by + bh) * h))
            plate_color = PLATE_COLORS[item.state]
            cv2.rectangle(view, p1, p2, plate_color, max(2, round(3 * scale)))
            text = f"{item.id} {item.state.replace('_', ' ')}"
            if item.fill_ratio is not None:
                text += f" {round(item.fill_ratio * 100)}%"
            _label(view, text, (p1[0], max(p1[1] - 8, 20)), plate_color, 0.6 * scale, 2)

        minutes = (
            0 if table.since is None else int((now - table.since).total_seconds() // 60)
        )
        status_text = TABLE_LABELS[table.status]
        _label(
            view,
            f"{zone.name}: {status_text} - {minutes} min",
            (x0 + 8, y0 + round(34 * scale)),
            color,
            0.9 * scale,
            2,
        )
    _label(view, header, (12, round(36 * scale)), (255, 255, 255), 0.8 * scale, 2)
    return view


def _open(source: str) -> tuple[cv2.VideoCapture, bool]:
    live = source.isdigit() or source.startswith(("http://", "https://", "rtsp://"))
    cap = cv2.VideoCapture(int(source) if source.isdigit() else source)
    if not cap.isOpened():
        raise OSError(f"No se pudo abrir la fuente: {source}")
    return cap, live


def run(args) -> int:
    pipeline = build_pipeline(args)
    if pipeline is None:
        return 2
    cap, live = _open(args.source)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    speed = args.speed or 1.0
    state = DemoState(pipeline.zones)
    worker = ThreadPoolExecutor(max_workers=1)
    pending: Future | None = None
    pending_ts: datetime | None = None
    writer = None
    start = datetime.now(UTC)
    t0 = time.monotonic()
    next_reading = 0.0
    frame_index = 0
    paused = False
    if not args.no_window:
        cv2.namedWindow(WINDOW, cv2.WINDOW_NORMAL)

    try:
        while True:
            if not paused:
                ok, frame = cap.read()
                if not ok:
                    break
                video_s = time.monotonic() - t0 if live else frame_index / fps
                ts = datetime.now(UTC) if live else start + timedelta(seconds=video_s)
                frame_index += 1

                if pending is not None and pending.done():
                    pending.result()
                    state.update(pipeline, pending_ts)
                    pending = None
                if video_s >= next_reading:
                    if pending is not None:  # el pipeline va atrasado: el video espera
                        pending.result()
                        state.update(pipeline, pending_ts)
                    pending = worker.submit(pipeline.process_frame, frame.copy(), ts)
                    pending_ts = ts
                    next_reading += args.interval

                header = (
                    f"Servio - lectura {state.readings} - cada {args.interval:g} s"
                    f" - video {int(video_s // 60):02d}:{int(video_s % 60):02d}"
                )
                image = draw_overlay(frame, pipeline.zones, state, ts, header)
                if args.record:
                    if writer is None:
                        args.record.parent.mkdir(parents=True, exist_ok=True)
                        h, w = image.shape[:2]
                        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
                        writer = cv2.VideoWriter(str(args.record), fourcc, fps, (w, h))
                    writer.write(image)

            if args.no_window:
                continue
            cv2.imshow(WINDOW, image)
            # espera lo justo para reproducir a `speed` (los videos en vivo, sin espera)
            delay = 1
            if not live and not paused:
                due = t0 + (frame_index / fps) / speed
                delay = max(1, round((due - time.monotonic()) * 1000))
            key = cv2.waitKey(delay) & 0xFF
            if key in (ord("q"), 27):
                break
            if key == ord(" "):
                paused = not paused
            if cv2.getWindowProperty(WINDOW, cv2.WND_PROP_VISIBLE) < 1:
                break
    except KeyboardInterrupt:
        logger.info("Cortado a mano")
    finally:
        if pending is not None:
            pending.result()
        worker.shutdown()
        pipeline.close()
        cap.release()
        if writer is not None:
            writer.release()
            logger.info("Video guardado en %s", args.record)
        cv2.destroyAllWindows()
    logger.info("%d lecturas", state.readings)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser("Servio: vista de la demo.")
    parser.add_argument("--record", type=Path, help="guardar la vista en un video .mp4")
    parser.add_argument(
        "--no-window", action="store_true", help="sin ventana (con --record)"
    )
    args = parser.parse_args(argv)
    setup_logging()
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
