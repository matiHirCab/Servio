"""Chequeo de encuadre de la cámara (B-4): la mesa entera, con luz y nítida.

Se corre sobre la primera lectura (`app.run`) y avisa, sin cortar, de lo que
haría que YOLO y Claude lean mal: imagen oscura o quemada, desenfocada, una mesa
que sale cortada o pegada al borde del cuadro, que se ve muy chica, o ninguna
mesa. Los umbrales salen de las pruebas con el video de la demo y las fotos de
salones; la guía de montaje está en docs/montaje-camara.md.

Antes de correr todo Servio, el mismo chequeo a mano, con los mismos flags y el
mismo toml que `main.py` (así mira la misma cámara, las mismas zonas y el mismo
detector de caras):

    python -m servio_vision.framing --config config/casa-jessica.toml
    python -m servio_vision.framing --source http://<IP>:4747/video [--zones zones.json]

Sale con 1 si encontró algún problema, así sirve para la checklist de cada casa.
"""

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
ANALYSIS_WIDTH = 640  # la nitidez se mide a este ancho como mucho (nunca se agranda)
EDGE_FRACTION = (
    0.01  # una mesa a menos de esto del borde (fracción del lado) está cortada
)
MIN_TABLE_FRACTION = (
    0.04  # una mesa con menos área que esto (del cuadro) se ve muy chica
)
# Con detección automática, una "mesa" que ocupa casi todo el cuadro es el
# comodín del tracker cuando no encontró ninguna (tables._whole_frame).
WHOLE_FRAME_FRACTION = 0.95


@dataclass(frozen=True)
class Measures:
    brightness: float  # brillo medio del cuadro
    sharpness: float  # varianza del laplaciano


def measure(frame: np.ndarray) -> Measures:
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    height, width = gray.shape
    if width > ANALYSIS_WIDTH:
        # achicar sí (que no dependa de la resolución); agrandar no: interpolar
        # suaviza y una imagen chica y nítida saldría "desenfocada"
        scale = ANALYSIS_WIDTH / width
        gray = cv2.resize(
            gray,
            (ANALYSIS_WIDTH, max(1, round(height * scale))),
            interpolation=cv2.INTER_AREA,
        )
    return Measures(
        brightness=float(gray.mean()),
        sharpness=float(cv2.Laplacian(gray, cv2.CV_64F).var()),
    )


def problems(
    frame: np.ndarray, zones: Zones | None, measures: Measures | None = None
) -> list[str]:
    """Lo que está mal en el encuadre, en palabras para quien arma la cámara. Vacío
    si está todo bien. `zones`: las mesas (fijas o detectadas); `None` si todavía
    no se sabe dónde están. `measures`: brillo y nitidez ya medidos, si se tienen."""
    found: list[str] = []
    m = measures or measure(frame)
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
    no_table = "No se ve ninguna mesa: apuntá la cámara a la mesa, desde arriba."
    if zones is None or not zones.tables:
        found.append(no_table)
        return found
    for zone in zones.tables:
        try:
            x0, y0, x1, y1 = table_rect(frame, zone, zones)
        except ValueError:
            found.append(f"{zone.name}: la zona queda fuera del cuadro.")
            continue
        area = (x1 - x0) * (y1 - y0)
        if area >= width * height * WHOLE_FRAME_FRACTION:
            found.append(no_table)
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
            # las mesas detectadas ya traen un 10 % de margen: tocar el borde es no
            # tener el aire que pide la guía, aunque la mesa entre justo
            found.append(
                f"{zone.name} sale cortada o pegada al borde {' y '.join(sides)}: "
                "subí la cámara o alejala, con un 10 % de aire alrededor de la mesa."
            )
        if area < width * height * MIN_TABLE_FRACTION:
            found.append(
                f"{zone.name} se ve muy chica ({(x1 - x0)}×{(y1 - y0)} px): acercá la "
                "cámara o usá más resolución; los platos tienen que medir ≥ 60 px."
            )
    return found


def log_report(frame: np.ndarray, zones: Zones | None, log=logger) -> list[str]:
    """El chequeo al arrancar: cada problema como una advertencia; si no hay, una
    línea que dice que el encuadre está bien."""
    m = measure(frame)
    found = problems(frame, zones, m)
    if not found:
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
    from servio_vision.cli import build_parser, load_settings
    from servio_vision.settings import explain
    from servio_vision.zones import load_zones

    args = build_parser("Chequeo de encuadre de la cámara (B-4).").parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    settings = load_settings(args)  # los mismos flags y toml que main.py
    if settings is None:
        return 2
    try:
        if settings.zones is not None:
            zones = load_zones(settings.zones)
            detect = None
        else:
            # antes de abrir la cámara: si falta ultralytics, que no espere al warm-up
            from servio_vision.tables import TableDetector, TableTracker

            detect = TableTracker(TableDetector()).update
        first = next(open_source(settings.source, face_model=settings.face_model), None)
        if first is None:
            print(f"La fuente {settings.source} no dio ningún frame.", file=sys.stderr)
            return 2
        frame, _ = first
        if detect is not None:
            zones = detect(frame)
    except OSError as e:  # fuente que no abre, modelo de caras, zones.json que no está
        print(e, file=sys.stderr)
        return 2
    except ValueError as e:  # zones.json inválido
        print(f"Zonas: {explain(e)}", file=sys.stderr)
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
