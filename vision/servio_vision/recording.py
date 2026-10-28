"""Grabar los frames que se procesan (`main.py --record DIR`).

Solo con `--record` queda alguna imagen en disco: un JPEG por lectura, con su hora
en el nombre (`20261028T203015.250Z.jpg`), a la cadencia del loop y no el video
entero. Los frames llegan ya anonimizados de `capture.py` (C-3), así que las
grabaciones tampoco tienen caras. Las sesiones van a la carpeta compartida del
equipo, nunca al repo (`vision/out/` está ignorado).

B-1 suma a esta carpeta el log de eventos y `servio replay DIR`.
"""

import logging
from datetime import datetime
from pathlib import Path

import numpy as np

from servio_vision.zones import to_jpeg

logger = logging.getLogger(__name__)

JPEG_QUALITY = 90  # más que los recortes (85): es lo que se guarda para reproducir


class FrameRecorder:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)
        self.saved = 0
        logger.info("Grabando los frames procesados en %s", directory)

    def save(self, frame: np.ndarray, ts: datetime) -> Path:
        path = self.directory / frame_name(ts)
        # to_jpeg + write_bytes y no cv2.imwrite: en Windows, imwrite no escribe
        # rutas con tildes o eñes (`out/grabación`)
        path.write_bytes(to_jpeg(frame, JPEG_QUALITY))
        self.saved += 1
        return path


def frame_name(ts: datetime) -> str:
    """`ts` UTC → `20261028T203015.250Z.jpg` (ordena bien y no repite en la lectura)."""
    return ts.strftime("%Y%m%dT%H%M%S.%f")[:-3] + "Z.jpg"
