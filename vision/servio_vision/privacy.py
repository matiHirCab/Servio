"""Privacidad: caras difuminadas apenas se lee el frame (C-3).

Se hace en un solo lugar, en `capture.py`, con un detector de caras **local**: así la
vista de reconocimiento, las grabaciones (`--record`), las hojas de `probar_video` y
cualquier imagen que termine en `out/` nunca ven una cara. Ver docs/legal-uruguay.md:
mientras el frame tenga personas identificables es videovigilancia.

Detector: YuNet, el de OpenCV (`cv2.FaceDetectorYN`). El modelo (ONNX, ~230 KB) se
baja una sola vez a `vision/models/` (ignorado por git) y se verifica su SHA-256; otra
ruta con `FACE_MODEL` en el `.env`. Sin el modelo y sin internet, el programa no
arranca: antes que mostrar caras, no mostrar nada.

Prueba manual:
    python -m servio_vision.privacy --source <foto | video | 0> [--out out/privacidad]
"""

import argparse
import hashlib
import logging
import os
import shutil
import sys
import urllib.request
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Protocol

import cv2
import numpy as np
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

VISION_DIR = Path(__file__).resolve().parents[1]
MODEL_NAME = "face_detection_yunet_2023mar.onnx"
MODEL_PATH = VISION_DIR / "models" / MODEL_NAME
MODEL_URL = (
    "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/"
    + MODEL_NAME
)
MODEL_SHA256 = "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"
MODEL_ENV = "FACE_MODEL"
DOWNLOAD_TIMEOUT_S = 30  # una red con portal cautivo no tiene que colgar el arranque
ENV_FILE = VISION_DIR.parent / ".env"

# El detector corre sobre el frame llevado a este ancho (se agranda o se achica).
# Desde una cámara en alto las caras miden 10–20 px en un frame de 640: YuNet no las
# ve por debajo de ~20 px, y a 1280 encuentra el doble (probado con fotos de salones).
# Cuesta ~120 ms en un frame 1080p: nada para una lectura cada 30 s.
DETECT_WIDTH = 1280
SCORE_THRESHOLD = 0.4  # bajo: caras chicas, de costado o desde arriba; un falso
# positivo chico solo pixela un pedacito de pared
NMS_THRESHOLD = 0.3
# Una "cara" más alta que esto (fracción del alto del frame) no es una cara desde una
# cámara en alto: es un torso o una mesa, y pixelarla taparía platos.
MAX_FACE_FRACTION = 0.2
MARGIN = 0.35  # la caja se agranda esto de cada lado: pelo, orejas, mentón
BLOCKS = 6  # mosaico: la cara queda en ~6×6 bloques, irreconocible

Box = tuple[int, int, int, int]  # x, y, ancho, alto, en píxeles del frame
Anonymizer = Callable[[np.ndarray], np.ndarray]


class PrivacyError(RuntimeError):
    """No se puede difuminar: falta el modelo o el OpenCV instalado no lo soporta."""


class FaceDetector(Protocol):
    def detect(self, frame: np.ndarray) -> list[Box]:
        """Cajas de las caras del frame (BGR), en píxeles del frame."""
        ...


class YuNetDetector:
    """`cv2.FaceDetectorYN` sobre el frame llevado a `DETECT_WIDTH` de ancho."""

    def __init__(self, model: Path, detect_width: int = DETECT_WIDTH) -> None:
        if not hasattr(cv2, "FaceDetectorYN"):
            raise PrivacyError(
                "Este OpenCV no trae FaceDetectorYN: pip install -r requirements.txt"
            )
        try:
            self._net = cv2.FaceDetectorYN.create(
                str(model), "", (320, 320), SCORE_THRESHOLD, NMS_THRESHOLD, 5000
            )
        except cv2.error as e:
            raise PrivacyError(f"No se pudo cargar el detector de caras {model}: {e}")
        self.detect_width = detect_width
        self._size: tuple[int, int] | None = None

    def detect(self, frame: np.ndarray) -> list[Box]:
        height, width = frame.shape[:2]
        if height == 0 or width == 0:
            return []  # frame vacío (cámara que se cortó): no hay nada que tapar
        scale = self.detect_width / width
        resized = (
            cv2.resize(frame, (self.detect_width, max(1, round(height * scale))))
            if scale != 1
            else frame
        )
        size = (resized.shape[1], resized.shape[0])
        try:
            if size != self._size:
                self._net.setInputSize(size)
                self._size = size
            _, faces = self._net.detect(resized)
        except cv2.error as e:
            # que se note que falló el detector, no la cámara
            raise PrivacyError(f"El detector de caras falló: {e}") from e
        if faces is None:
            return []
        max_height = height * MAX_FACE_FRACTION
        boxes = [
            (int(x / scale), int(y / scale), int(w / scale) + 1, int(h / scale) + 1)
            for x, y, w, h in faces[:, :4]
        ]
        return [b for b in boxes if b[3] <= max_height]


def model_path(env: dict[str, str] | None = None) -> Path:
    env = os.environ if env is None else env
    raw = (env.get(MODEL_ENV) or "").strip()
    return Path(raw) if raw else MODEL_PATH


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _download(url: str, destination: Path, timeout: float = DOWNLOAD_TIMEOUT_S) -> None:
    with (
        urllib.request.urlopen(url, timeout=timeout) as response,
        destination.open("wb") as file,
    ):
        shutil.copyfileobj(response, file)


def ensure_model(path: Path = MODEL_PATH) -> Path:
    """Devuelve la ruta del modelo; si no está, lo baja y verifica el hash."""
    if path.exists():
        return path
    if path != MODEL_PATH:
        raise PrivacyError(f"No existe el modelo de caras {MODEL_ENV}={path}")
    logger.info("Bajando el detector de caras a %s", path)
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(".part")
    try:
        _download(MODEL_URL, partial)
    except OSError as e:
        partial.unlink(missing_ok=True)
        raise PrivacyError(
            f"No se pudo bajar el detector de caras ({e}). Sin internet, copiá "
            f"{MODEL_NAME} a {path} desde la carpeta del equipo."
        ) from e
    digest = _sha256(partial)
    if digest != MODEL_SHA256:
        partial.unlink()
        raise PrivacyError(
            f"El detector de caras bajado no es el esperado (sha256 {digest})"
        )
    partial.replace(path)
    return path


def load_detector() -> FaceDetector:
    # FACE_MODEL del .env también para las herramientas, que no pasan por main.py
    # (A-1 lo lleva a Settings). No pisa variables ya puestas.
    load_dotenv(ENV_FILE)
    return YuNetDetector(ensure_model(model_path()))


def pixelate(
    frame: np.ndarray,
    boxes: Sequence[Box],
    margin: float = MARGIN,
    blocks: int = BLOCKS,
) -> np.ndarray:
    """Mosaico sobre cada caja (agrandada `margin` de cada lado), **en el mismo
    frame**; lo devuelve. Las cajas que se salen del frame se recortan a él."""
    height, width = frame.shape[:2]
    for x, y, w, h in boxes:
        dx, dy = int(w * margin), int(h * margin)
        x0, y0 = max(0, x - dx), max(0, y - dy)
        x1, y1 = min(width, x + w + dx), min(height, y + h + dy)
        if x1 <= x0 or y1 <= y0:
            continue
        region = frame[y0:y1, x0:x1]
        small = cv2.resize(region, (blocks, blocks), interpolation=cv2.INTER_AREA)
        frame[y0:y1, x0:x1] = cv2.resize(
            small, (x1 - x0, y1 - y0), interpolation=cv2.INTER_NEAREST
        )
    return frame


class FaceBlur:
    """Anonimizador: detecta y pixela las caras de cada frame. Cuenta cuántas vio.

    `every`: detectar cada tantos frames y, en los del medio, pixelar las últimas
    cajas. Solo para una vista fluida (`vista_demo`, 25–30 fps): las caras se
    mueven poco en 2 frames y el margen las sigue tapando. Las lecturas del pipeline
    y las grabaciones de `main.py` van con 1: cada frame se detecta.
    """

    def __init__(self, detector: FaceDetector, every: int = 1) -> None:
        if every < 1:
            raise ValueError(f"every tiene que ser al menos 1 (vino {every})")
        self.detector = detector
        self.every = every
        self.faces = 0
        self._frames = 0
        self._boxes: list[Box] = []

    def __call__(self, frame: np.ndarray) -> np.ndarray:
        if self._frames % self.every == 0:
            self._boxes = self.detector.detect(frame)
            if self._boxes:
                self.faces += len(self._boxes)
                logger.debug("%d cara(s) difuminada(s)", len(self._boxes))
        self._frames += 1
        if self._boxes:
            pixelate(frame, self._boxes)
        return frame


def default_anonymizer(every: int = 1) -> FaceBlur:
    """El que usa `capture.py` si no le pasan otro."""
    return FaceBlur(load_detector(), every)


def main(argv: list[str] | None = None) -> int:
    from servio_vision.capture import IMAGE_SUFFIXES, open_source

    parser = argparse.ArgumentParser(description="Muestra qué caras se difuminan.")
    parser.add_argument("--source", required=True, help="foto, video, carpeta, 0 o URL")
    parser.add_argument("--out", type=Path, default=Path("out/privacidad"))
    parser.add_argument("--limit", type=int, default=5, help="cuántos frames guardar")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    blur = default_anonymizer()
    path = Path(args.source)
    if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES:
        frames = iter([blur(cv2.imread(str(path)))])  # una foto suelta
    else:
        frames = (frame for frame, _ in open_source(args.source, anonymize=blur))
    args.out.mkdir(parents=True, exist_ok=True)
    seen = 0
    for n, frame in enumerate(frames, 1):
        out = args.out / f"frame_{n:03d}.jpg"
        cv2.imwrite(str(out), frame)
        print(f"{out}: {blur.faces - seen} cara(s)")
        seen = blur.faces
        if n >= args.limit:
            break
    return 0


if __name__ == "__main__":
    sys.exit(main())
