"""Captura de frames desde video, carpeta de fotos o cámara (JES-3).

Todas las fuentes se recorren igual: `for frame, ts in open_source(...)`.
- `frame`: imagen BGR de OpenCV (numpy), en la resolución original.
- `ts`: `datetime` con zona UTC.
  - Video: tiempo del video sumado a `start` (no el reloj), para que los
    umbrales de las reglas tengan sentido aunque se reproduzca más rápido.
    Si el video dice unos fps imposibles (los `.asf` de cámaras de seguridad
    dicen 1000), se avanza por el tiempo de cada frame en vez de por fps.
  - Carpeta: una foto cada `interval` segundos a partir de `start`.
  - Cámara o URL del celular: reloj real.

Prueba manual:
    python -m servio_vision.capture --source <video | carpeta | 0 | url> [--interval 1]
"""

import argparse
import logging
import math
import sys
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import cv2
import numpy as np

logger = logging.getLogger(__name__)

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
STREAM_PREFIXES = ("http://", "https://", "rtsp://", "rtmp://")
MAX_PLAUSIBLE_FPS = 120  # más que esto es un dato roto del contenedor

Frame = tuple[np.ndarray, datetime]


def open_source(
    source: str,
    interval: float = 1.0,
    speed: float | None = None,
    start: datetime | None = None,
) -> Iterator[Frame]:
    """Iterador de `(frame, ts)` según el tipo de `source`.

    `interval`: segundos entre frames (del video, de la carpeta o del reloj).
    `speed`: solo video y carpeta; si se pasa, espera para reproducir a esa
    velocidad (1 = tiempo real, 10 = diez veces más rápido). Sin `speed` va
    lo más rápido posible.
    `start`: ts del primer frame de un video o carpeta (por defecto, ahora).
    """
    if interval <= 0:
        raise ValueError("interval tiene que ser mayor a 0")
    start = start or datetime.now(UTC)
    if source.isdigit() or source.startswith(STREAM_PREFIXES):
        return read_camera(int(source) if source.isdigit() else source, interval)
    path = Path(source)
    if path.is_dir():
        return read_folder(path, interval, speed, start)
    if path.is_file():
        return read_video(path, interval, speed, start)
    raise FileNotFoundError(f"No existe la fuente: {source}")


def read_video(
    path: Path, interval: float, speed: float | None, start: datetime
) -> Iterator[Frame]:
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise OSError(f"No se pudo abrir el video: {path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    pacer = _Pacer(speed)
    try:
        if fps > MAX_PLAUSIBLE_FPS:
            logger.warning(
                "El video dice %.0f fps; avanzo por el tiempo de cada frame", fps
            )
            yield from _read_video_by_time(cap, interval, pacer, start)
        else:
            yield from _read_video_by_fps(cap, fps, interval, pacer, start)
    finally:
        cap.release()


def _read_video_by_fps(
    cap: cv2.VideoCapture,
    fps: float,
    interval: float,
    pacer: "_Pacer",
    start: datetime,
) -> Iterator[Frame]:
    step = max(1, round(interval * fps))  # frames entre lecturas
    index = 0  # índice del próximo frame que devuelve cap.grab()
    target = 0
    while True:
        # grab() avanza sin decodificar: saltar así es más rápido y
        # más preciso que buscar por tiempo con CAP_PROP_POS_MSEC.
        while index < target:
            if not cap.grab():
                return
            index += 1
        ok, frame = cap.read()
        if not ok:
            return
        index += 1
        video_seconds = target / fps
        pacer.wait(video_seconds)
        yield frame, start + timedelta(seconds=video_seconds)
        target += step


def _read_video_by_time(
    cap: cv2.VideoCapture, interval: float, pacer: "_Pacer", start: datetime
) -> Iterator[Frame]:
    """Para videos con fps falsos: el tiempo de cada frame sale de
    CAP_PROP_POS_MSEC, que en esos contenedores sí es el real."""
    target = 0.0  # segundo del video de la próxima lectura
    last = 0.0
    while True:
        if not cap.grab():
            return
        seconds = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000
        # algunos frames vienen sin tiempo (0): se toma el del anterior
        seconds = max(seconds, last)
        last = seconds
        if seconds < target:
            continue
        ok, frame = cap.retrieve()
        if not ok:
            return
        pacer.wait(seconds)
        yield frame, start + timedelta(seconds=seconds)
        # si el video salta más de un intervalo, no repite el frame
        target = (math.floor(seconds / interval) + 1) * interval


def read_folder(
    path: Path, interval: float, speed: float | None, start: datetime
) -> Iterator[Frame]:
    files = sorted(p for p in path.iterdir() if p.suffix.lower() in IMAGE_SUFFIXES)
    if not files:
        raise FileNotFoundError(f"No hay imágenes en {path}")
    pacer = _Pacer(speed)
    for i, file in enumerate(files):
        frame = cv2.imread(str(file))
        if frame is None:
            logger.warning("No se pudo leer %s, la salteo", file)
            continue
        seconds = i * interval
        pacer.wait(seconds)
        yield frame, start + timedelta(seconds=seconds)


def read_camera(source: int | str, interval: float) -> Iterator[Frame]:
    cap = cv2.VideoCapture(source)
    if not cap.isOpened():
        raise OSError(f"No se pudo abrir la cámara: {source}")
    last = -interval
    try:
        while True:
            # Se lee siempre, aunque no toque devolver, para que el buffer
            # de la cámara no se llene y el frame entregado sea el actual.
            ok, frame = cap.read()
            if not ok:
                logger.warning("La cámara dejó de mandar frames: %s", source)
                return
            now = time.monotonic()
            if now - last >= interval:
                last = now
                yield frame, datetime.now(UTC)
    finally:
        cap.release()


class _Pacer:
    """Espera para que `seconds` de la fuente pasen a `speed` en el reloj."""

    def __init__(self, speed: float | None) -> None:
        if speed is not None and speed <= 0:
            raise ValueError("speed tiene que ser mayor a 0")
        self.speed = speed
        self.t0 = time.monotonic()

    def wait(self, seconds: float) -> None:
        if self.speed is None:
            return
        delay = self.t0 + seconds / self.speed - time.monotonic()
        if delay > 0:
            time.sleep(delay)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Muestra los frames de una fuente.")
    parser.add_argument("--source", required=True, help="video, carpeta, 0 o URL")
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--speed", type=float, default=None)
    parser.add_argument("--limit", type=int, default=None, help="cortar tras N frames")
    args = parser.parse_args(argv)

    count = 0
    for frame, ts in open_source(args.source, args.interval, args.speed):
        height, width = frame.shape[:2]
        print(f"{ts.isoformat(timespec='milliseconds')}  {width}x{height}")
        count += 1
        if args.limit is not None and count >= args.limit:
            break
    print(f"{count} frames", file=sys.stderr)
    return 0 if count else 1


if __name__ == "__main__":
    sys.exit(main())
