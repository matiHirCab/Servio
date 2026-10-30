"""Chequeo de encuadre de la cámara (B-4): la mesa entera, con luz y nítida.

Se corre sobre la primera lectura (`app.run`) y avisa, sin cortar, de lo que
haría que YOLO y Claude lean mal: imagen oscura o quemada, desenfocada, una mesa
que sale cortada o pegada al borde del cuadro, que se ve muy chica, o ninguna
mesa. La luz se chequea **antes** de clasificar el primer frame (un cuadro negro
no vale una llamada al modelo); las mesas, después, cuando ya se detectaron.
Brillo y nitidez se miden sobre todo el cuadro: la mesa vacía y lisa de antes de
servir no tiene textura, pero sus bordes y las sillas sí. Los umbrales salen de las
pruebas con el video de la demo y las fotos de salones; la guía de montaje está en
docs/montaje-camara.md.

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

NO_TABLE = "No se ve ninguna mesa: apuntá la cámara a la mesa, desde arriba."


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


def light_problems(frame: np.ndarray, measures: Measures | None = None) -> list[str]:
    """Oscura, quemada o desenfocada. No necesita saber dónde están las mesas."""
    m = measures or measure(frame)
    found: list[str] = []
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
    return found


def table_problems(frame: np.ndarray, zones: Zones | None) -> list[str]:
    """Mesa cortada o pegada al borde, muy chica, fuera del cuadro, o ninguna.
    `zones`: las mesas (fijas, o detectadas); `None` si no se encontró ninguna."""
    if zones is None or not zones.tables:
        return [NO_TABLE]
    height, width = frame.shape[:2]
    found: list[str] = []
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
            # las mesas detectadas ya traen un 10 % de margen: tocar el borde es no
            # tener el aire que pide la guía, aunque la mesa entre justo
            found.append(
                f"{zone.name} sale cortada o pegada al borde {' y '.join(sides)}: "
                "subí la cámara o alejala, con un 10 % de aire alrededor de la mesa."
            )
        if (x1 - x0) * (y1 - y0) < width * height * MIN_TABLE_FRACTION:
            found.append(
                f"{zone.name} se ve muy chica ({(x1 - x0)}×{(y1 - y0)} px): acercá la "
                "cámara o usá más resolución; los platos tienen que medir ≥ 60 px."
            )
    return found


def problems(frame: np.ndarray, zones: Zones | None) -> list[str]:
    """Todo junto: luz y mesas. Vacío si el encuadre está bien."""
    return light_problems(frame) + table_problems(frame, zones)


def log_report(
    frame: np.ndarray,
    zones: Zones | None,
    log=logger,
    *,
    light: bool = True,
    tables: bool = True,
) -> list[str]:
    """El chequeo al arrancar: cada problema como una advertencia; si no hubo
    ninguno (en esta parte), una línea que dice que está bien. `app.run` lo llama
    dos veces: la luz antes de clasificar el primer frame, las mesas después."""
    m = measure(frame) if light else None
    found = (light_problems(frame, m) if light else []) + (
        table_problems(frame, zones) if tables else []
    )
    for text in found:
        log.warning("Encuadre: %s", text)
    if not found and tables:
        log.info("Encuadre OK: %d mesa(s)", len(zones.tables) if zones else 0)
    if not found and light:
        assert m is not None  # con `light`, `m` se midió arriba
        log.info("Luz OK: brillo %.0f, nitidez %.0f", m.brightness, m.sharpness)
    return found


def main(argv: list[str] | None = None) -> int:
    from servio_vision.app import load_tables, setup_logging
    from servio_vision.capture import open_source
    from servio_vision.cli import (
        add_record_flag,
        build_parser,
        load_settings,
        require_source,
    )
    from servio_vision.settings import explain

    parser = add_record_flag(build_parser("Chequeo de encuadre de la cámara (B-4)."))
    args = parser.parse_args(argv)  # los mismos flags y toml que main.py
    setup_logging(args.verbose)
    settings = load_settings(args)
    if settings is None or not require_source(settings):  # sin fuente, un mensaje
        return 2
    assert settings.source is not None  # lo garantiza `require_source`
    try:
        zones, tracker = load_tables(settings)  # ultralytics o zones.json, antes
        first = next(open_source(settings.source, face_model=settings.face_model), None)
        if first is None:
            print(f"La fuente {settings.source} no dio ningún frame.", file=sys.stderr)
            return 2
        frame, _ = first
        if tracker is not None:
            try:
                zones = tracker.update(frame)
            except Exception as e:  # noqa: BLE001 — pesos rotos, torch: no es encuadre
                print(f"El detector de mesas falló: {e}", file=sys.stderr)
                return 2
            if not tracker.tables:
                zones = None  # el tracker devuelve todo el cuadro como comodín
    except OSError as e:  # fuente que no abre, modelo de caras, zones.json que no está
        print(e, file=sys.stderr)
        return 2
    except ValueError as e:  # zones.json inválido
        print(f"Zonas: {explain(e)}", file=sys.stderr)
        return 2
    except ImportError as e:
        print(e, file=sys.stderr)
        return 2
    found = log_report(frame, zones)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
