"""Chequeo de encuadre de la cámara (B-4): la mesa entera, con luz y nítida.

Se corre sobre la primera lectura (`app.run`) y avisa, sin cortar, de lo que
haría que YOLO y Claude lean mal: imagen oscura o quemada, desenfocada, una mesa
que sale cortada por el borde del cuadro o que se ve muy chica, o ninguna mesa.
Los umbrales salen de las pruebas con el video de la demo y las fotos de
salones; la guía de montaje está en docs/montaje-camara.md.

Antes de correr todo Servio, el mismo chequeo a mano:

    python -m servio_vision.framing --source http://<IP>:4747/video [--zones zones.json]

Sale con 1 si encontró algún problema, así sirve para la checklist de cada casa.
"""

import argparse
import logging
import sys
from dataclasses import dataclass

import cv2
import numpy as np

from servio_vision.zones import Zones, table_rect

logger = logging.getLogger(__name__)

DARK_MEAN = 60  # brillo medio (0–255) por debajo del cual no se distingue la comida
BRIGHT_MEAN = 215  # por encima, la imagen está quemada (ventana, luz directa)
BLUR_VARIANCE = 40  # varianza del laplaciano (a 640 px de ancho) de una imagen nítida
ANALYSIS_WIDTH = 640  # la nitidez se mide siempre al mismo tamaño
EDGE_FRACTION = (
    0.01  # una mesa a menos de esto del borde (fracción del lado) está cortada
)
MIN_TABLE_FRACTION = (
    0.04  # una mesa con menos área que esto (del cuadro) se ve muy chica
)


@dataclass(frozen=True)
class Measures:
    brightness: float  # brillo medio del cuadro
    sharpness: float  # varianza del laplaciano


def measure(frame: np.ndarray) -> Measures:
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    height, width = gray.shape
    if width != ANALYSIS_WIDTH:
        scale = ANALYSIS_WIDTH / width
        gray = cv2.resize(gray, (ANALYSIS_WIDTH, max(1, round(height * scale))))
    return Measures(
        brightness=float(gray.mean()),
        sharpness=float(cv2.Laplacian(gray, cv2.CV_64F).var()),
    )


def problems(frame: np.ndarray, zones: Zones | None) -> list[str]:
    """Lo que está mal en el encuadre, en palabras para quien arma la cámara. Vacío
    si está todo bien. `zones`: las mesas (fijas o detectadas); `None` si todavía
    no se sabe dónde están."""
    found: list[str] = []
    m = measure(frame)
    if m.brightness < DARK_MEAN:
        found.append(
            f"Imagen oscura (brillo {m.brightness:.0f}/255): más luz sobre la mesa o "
            "subí la exposición de la cámara."
        )
    elif m.brightness > BRIGHT_MEAN:
        found.append(
            f"Imagen quemada (brillo {m.brightness:.0f}/255): sacá la ventana o la "
            "lámpara del cuadro, o bajá la exposición."
        )
    if m.sharpness < BLUR_VARIANCE:
        found.append(
            f"Imagen desenfocada (nitidez {m.sharpness:.0f}): limpiá la lente, fijá "
            "el foco en la mesa y revisá que la cámara no vibre."
        )
    height, width = frame.shape[:2]
    if zones is None or not zones.tables:
        found.append("No se ve ninguna mesa: apuntá la cámara a la mesa, desde arriba.")
        return found
    for zone in zones.tables:
        try:
            x0, y0, x1, y1 = table_rect(frame, zone, zones)
        except ValueError:
            found.append(f"{zone.name}: la zona queda fuera del cuadro.")
            continue
        sides = []
        if x0 <= width * EDGE_FRACTION:
            sides.append("izquierdo")
        if x1 >= width * (1 - EDGE_FRACTION):
            sides.append("derecho")
        if y0 <= height * EDGE_FRACTION:
            sides.append("de arriba")
        if y1 >= height * (1 - EDGE_FRACTION):
            sides.append("de abajo")
        if sides:
            found.append(
                f"{zone.name} sale cortada por el borde {' y '.join(sides)}: subí la "
                "cámara o alejala para que entre entera con aire alrededor."
            )
        if (x1 - x0) * (y1 - y0) < width * height * MIN_TABLE_FRACTION:
            found.append(
                f"{zone.name} se ve muy chica ({(x1 - x0)}×{(y1 - y0)} px): acercá la "
                "cámara o usá más resolución; los platos tienen que medir ≥ 60 px."
            )
    return found


def log_report(frame: np.ndarray, zones: Zones | None, log=logger) -> list[str]:
    """El chequeo al arrancar: cada problema como una advertencia; si no hay, una
    línea que dice que el encuadre está bien."""
    found = problems(frame, zones)
    if not found:
        m = measure(frame)
        log.info(
            "Encuadre OK: %d mesa(s), brillo %.0f, nitidez %.0f",
            len(zones.tables) if zones else 0,
            m.brightness,
            m.sharpness,
        )
    for text in found:
        log.warning("Encuadre: %s", text)
    return found


def main(argv: list[str] | None = None) -> int:
    from servio_vision.capture import open_source
    from servio_vision.zones import load_zones

    parser = argparse.ArgumentParser(description="Chequeo de encuadre de la cámara.")
    parser.add_argument("--source", required=True, help="cámara (0, URL), video o foto")
    parser.add_argument("--zones", help="zones.json; sin esto, las mesas se detectan")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    try:
        frame, _ = next(open_source(args.source))
        if args.zones:
            zones = load_zones(args.zones)
        else:
            from servio_vision.tables import TableDetector, TableTracker

            zones = TableTracker(TableDetector()).update(frame)
    except OSError as e:
        print(e, file=sys.stderr)
        return 2
    except ImportError:
        print(
            "Para detectar las mesas hace falta ultralytics (requirements-yolo.txt), "
            "o pasá --zones.",
            file=sys.stderr,
        )
        return 2
    found = log_report(frame, zones)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
