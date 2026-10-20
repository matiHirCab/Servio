"""Captura de frames desde video, carpeta de fotos o cámara (JES-3).

Todas las fuentes se recorren igual: `for frame, ts in open_source(...)`.
- `frame`: imagen BGR de OpenCV (numpy), en la resolución original.
- `ts`: `datetime` con zona UTC.
  - Video: tiempo del video sumado a `start` (no el reloj), para que los
    umbrales de las reglas tengan sentido aunque se reproduzca más rápido.
  - Carpeta: una foto cada `interval` segundos a partir de `start`.
  - Cámara o URL del celular: reloj real.

Prueba manual:
    python -m servio_vision.capture --source <video | carpeta | 0 | url> [--interval 1]
"""

import argparse
import logging
import sys
import threading
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import cv2
import numpy as np

logger = logging.getLogger(__name__)

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
STREAM_PREFIXES = ("http://", "https://", "rtsp://", "rtmp://")
CAMERA_WARMUP_S = 1.5  # frames de la cámara que se descartan al abrirla
CAMERA_RECONNECT_S = 60.0  # cuánto se reintenta, en total, una cámara que se cortó
CAMERA_RETRY_S = 2.0  # espera entre intentos de abrirla
STREAM_TIMEOUT_MS = (
    5000  # una apertura o lectura de un stream por red colgada corta acá
)
READ_FAILURES_TO_RECONNECT = 5  # lecturas fallidas seguidas antes de reconectar
READ_RETRY_S = 0.1  # espera entre lecturas fallidas

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


def declared_fps(cap: cv2.VideoCapture) -> float:
    """fps que declara el video, o 30 si no declara ninguno."""
    return cap.get(cv2.CAP_PROP_FPS) or 30.0


def frame_seconds(cap: cv2.VideoCapture, index: int, fps: float) -> float:
    """Segundo del video del último frame leído (después de `grab()`/`read()`).

    Se usa la marca de tiempo del frame: algunos videos (por ejemplo `.asf` de
    cámaras de seguridad) declaran fps falsos, como 1000, y contar frames da
    cualquier cosa. Si el backend no da marca de tiempo, se cuenta con los fps.
    """
    ms = cap.get(cv2.CAP_PROP_POS_MSEC)
    if ms > 0 or index == 0:
        return ms / 1000
    return index / fps


def read_video(
    path: Path, interval: float, speed: float | None, start: datetime
) -> Iterator[Frame]:
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise OSError(f"No se pudo abrir el video: {path}")
    fps = declared_fps(cap)
    pacer = _Pacer(speed)
    next_reading = 0.0  # segundo del video de la próxima lectura
    index = 0
    try:
        # grab() avanza sin decodificar; solo se decodifica (retrieve) el frame
        # de cada lectura.
        while cap.grab():
            video_seconds = frame_seconds(cap, index, fps)
            index += 1
            # medio frame de tolerancia: el frame más cercano a la hora pedida
            if video_seconds + 0.5 / fps < next_reading:
                continue
            ok, frame = cap.retrieve()
            if not ok:
                return
            pacer.wait(video_seconds)
            yield frame, start + timedelta(seconds=video_seconds)
            while next_reading <= video_seconds:
                next_reading += interval
    finally:
        cap.release()


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
    reader = CameraReader(source)
    reader.start()
    last = -interval
    try:
        # `frames()` da siempre el frame más nuevo: si el loop estuvo ocupado
        # clasificando, se entrega el actual, no uno de hace 10 s.
        for frame in reader.frames():
            now = time.monotonic()
            if now - last >= interval:
                last = now
                yield frame, datetime.now(UTC)
    finally:
        reader.stop()


class CameraReader:
    """Lee la cámara en un hilo aparte y se queda con el último frame.

    Se lee siempre, aunque el loop esté ocupado clasificando: si no, el buffer
    se llena, el frame entregado es viejo y un stream por red (DroidCam, IP
    Webcam) corta la conexión. Un fallo suelto de lectura se reintenta; con
    `READ_FAILURES_TO_RECONNECT` seguidos, reabre la cámara. Se da por vencida si
    pasan `CAMERA_RECONNECT_S` sin un frame bueno, aunque abra y se vuelva a cortar.
    Lo usan `read_camera` y `tools/vista_demo.py`.
    """

    def __init__(self, source: int | str) -> None:
        self.source = source
        self.fps = 30.0
        self._cond = threading.Condition()
        self._frame: np.ndarray | None = None
        self._seq = 0  # frames leídos
        self._taken = 0  # último frame entregado
        self._done = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Abre la cámara (`OSError` si no abre) y empieza a leer."""
        cap = self._open()
        if cap is None:
            raise OSError(f"No se pudo abrir la cámara: {self.source}")
        self.fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        self._thread = threading.Thread(target=self._run, args=(cap,), daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)

    def frames(self) -> Iterator[np.ndarray]:
        """Cada frame nuevo, hasta que la cámara se corta del todo. Requiere `start()`."""
        try:
            while (frame := self.latest()) is not None:
                yield frame
            logger.warning("La cámara dejó de mandar frames: %s", self.source)
        finally:
            self.stop()

    def latest(self) -> np.ndarray | None:
        """Espera un frame que no se haya entregado; `None` si la cámara terminó.

        No tiene tope: mientras el hilo esté reconectando, espera. Espera en
        tramos cortos porque en Windows una espera larga no deja que Ctrl+C
        corte el programa.
        """
        with self._cond:
            while self._seq <= self._taken and not self._done:
                self._cond.wait(timeout=0.5)
            if self._seq <= self._taken:
                return None
            self._taken = self._seq
            return self._frame

    def _open(self) -> cv2.VideoCapture | None:
        if isinstance(self.source, str):
            # Stream por red: sin tope, una lectura de un celular que perdió el Wi-Fi
            # puede quedar colgada ~30 s antes de fallar.
            timeouts = [
                cv2.CAP_PROP_OPEN_TIMEOUT_MSEC,
                STREAM_TIMEOUT_MS,
                cv2.CAP_PROP_READ_TIMEOUT_MSEC,
                STREAM_TIMEOUT_MS,
            ]
            cap = cv2.VideoCapture(self.source, cv2.CAP_FFMPEG, timeouts)
        else:
            cap = cv2.VideoCapture(self.source)
        if not cap.isOpened():
            cap.release()
            return None
        # Una webcam recién abierta manda frames negros hasta que ajusta la
        # exposición (~0,5 s): sin esto, la primera lectura es una mesa vacía.
        warmup_end = time.monotonic() + CAMERA_WARMUP_S
        while time.monotonic() < warmup_end and cap.read()[0]:
            pass
        # Abrir no alcanza: una webcam que usa otro programa abre pero no manda frames.
        if not cap.read()[0]:
            cap.release()
            return None
        return cap

    def _run(self, cap: cv2.VideoCapture) -> None:
        failures = 0
        down_since: float | None = None  # desde cuándo no llega un frame bueno
        try:
            while not self._stop.is_set():
                ok, frame = cap.read()
                if ok:
                    failures = 0
                    down_since = None
                    with self._cond:
                        self._frame = frame
                        self._seq += 1
                        self._cond.notify_all()
                    continue
                if down_since is None:
                    down_since = time.monotonic()
                # Por Wi-Fi, un paquete atrasado hace fallar una lectura suelta:
                # recién se reconecta si fallan varias seguidas.
                failures += 1
                if failures < READ_FAILURES_TO_RECONNECT:
                    self._stop.wait(READ_RETRY_S)
                    continue
                cap.release()
                cap = self._reconnect(down_since + CAMERA_RECONNECT_S)
                if cap is None:
                    break
                failures = 0
        finally:
            if cap is not None:
                cap.release()
            with self._cond:
                self._done = True
                self._cond.notify_all()

    def _reconnect(self, deadline: float) -> cv2.VideoCapture | None:
        """Reabre la cámara hasta `deadline` (`time.monotonic()`)."""
        logger.warning("La cámara se cortó, reintento: %s", self.source)
        while not self._stop.is_set() and time.monotonic() < deadline:
            cap = self._open()
            if cap is not None:
                logger.info("Cámara reconectada: %s", self.source)
                return cap
            self._stop.wait(CAMERA_RETRY_S)
        return None


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
