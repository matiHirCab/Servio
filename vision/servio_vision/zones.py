"""Zonas de mesas y recorte por mesa (JES-4).

Formato de `config/zones.json` en docs/context/03-contratos.md («Zonas de mesas»).
El recorte es el rectángulo que encierra el polígono de la mesa, sacado del
frame original (sin achicar) y codificado en JPEG calidad 85 (04-vision.md).
Si el video tiene otra resolución que la usada al marcar las zonas, los
polígonos se escalan.

Prueba manual (guarda un recorte por mesa en out/crops/):
    python -m servio_vision.zones --source <video> [--zones config/zones.json]
"""

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np
from pydantic import BaseModel, Field, field_validator

JPEG_QUALITY = 85
DEFAULT_ZONES = Path("config/zones.json")


class TableZone(BaseModel):
    table_id: str = Field(min_length=1)
    name: str
    polygon: list[tuple[int, int]]

    @field_validator("polygon")
    @classmethod
    def _at_least_three_points(cls, polygon: list[tuple[int, int]]):
        if len(polygon) < 3:
            raise ValueError("el polígono de una mesa necesita al menos 3 puntos")
        return polygon


class Zones(BaseModel):
    camera_id: str = "cam-1"
    frame_size: tuple[int, int]  # [ancho, alto] del frame en que se marcaron
    tables: list[TableZone]

    @field_validator("tables")
    @classmethod
    def _unique_ids(cls, tables: list[TableZone]):
        ids = [t.table_id for t in tables]
        if len(ids) != len(set(ids)):
            raise ValueError("hay table_id repetidos")
        return tables


def load_zones(path: Path = DEFAULT_ZONES) -> Zones:
    return Zones.model_validate_json(Path(path).read_text(encoding="utf-8"))


def save_zones(zones: Zones, path: Path = DEFAULT_ZONES) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(zones.model_dump_json(indent=2) + "\n", encoding="utf-8")


def scaled_polygon(zone: TableZone, zones: Zones, frame: np.ndarray) -> np.ndarray:
    """Polígono en píxeles del frame actual (escala si cambió la resolución)."""
    height, width = frame.shape[:2]
    sx = width / zones.frame_size[0]
    sy = height / zones.frame_size[1]
    return np.array([(round(x * sx), round(y * sy)) for x, y in zone.polygon], np.int32)


def table_rect(
    frame: np.ndarray, zone: TableZone, zones: Zones
) -> tuple[int, int, int, int]:
    """(x0, y0, x1, y1) del rectángulo de la mesa, recortado al borde del frame.

    Es el área del recorte que recibe el clasificador: las `bbox` de los platos
    están normalizadas dentro de este rectángulo.
    """
    height, width = frame.shape[:2]
    x, y, w, h = cv2.boundingRect(scaled_polygon(zone, zones, frame))
    x0, y0 = max(x, 0), max(y, 0)
    x1, y1 = min(x + w, width), min(y + h, height)
    if x1 <= x0 or y1 <= y0:
        raise ValueError(f"La mesa {zone.table_id} queda fuera del frame")
    return x0, y0, x1, y1


# Lado mínimo (fracción del recorte) para que una caja recortada siga siendo un plato.
MIN_BOX_SIDE = 0.01


def remap_bbox(
    bbox: tuple[float, float, float, float],
    old: tuple[int, int, int, int],
    new: tuple[int, int, int, int],
) -> tuple[float, float, float, float] | None:
    """Caja [x, y, w, h] normalizada en el recorte `old` → la misma caja en `new`.

    `old`/`new` son (x0, y0, x1, y1) en píxeles del frame (`table_rect`). Si la caja
    queda en parte afuera del recorte nuevo, se recorta; si queda (casi) toda
    afuera, devuelve `None`: ese plato ya no está en la mesa.
    """
    bx, by, bw, bh = bbox
    ow, oh = old[2] - old[0], old[3] - old[1]
    nw, nh = new[2] - new[0], new[3] - new[1]
    x0 = (old[0] + bx * ow - new[0]) / nw
    y0 = (old[1] + by * oh - new[1]) / nh
    x1 = x0 + bw * ow / nw
    y1 = y0 + bh * oh / nh
    x0, y0 = max(x0, 0.0), max(y0, 0.0)
    x1, y1 = min(x1, 1.0), min(y1, 1.0)
    if x1 - x0 < MIN_BOX_SIDE or y1 - y0 < MIN_BOX_SIDE:
        return None
    return (x0, y0, x1 - x0, y1 - y0)


def crop_table(frame: np.ndarray, zone: TableZone, zones: Zones) -> np.ndarray:
    """Rectángulo que encierra la mesa, recortado al borde del frame."""
    x0, y0, x1, y1 = table_rect(frame, zone, zones)
    return frame[y0:y1, x0:x1]


def to_jpeg(image: np.ndarray, quality: int = JPEG_QUALITY) -> bytes:
    ok, buffer = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise ValueError("No se pudo codificar el recorte en JPEG")
    return buffer.tobytes()


def crop_tables(frame: np.ndarray, zones: Zones) -> dict[str, bytes]:
    """Recorte JPEG de cada mesa, listo para `Classifier.classify`."""
    return {t.table_id: to_jpeg(crop_table(frame, t, zones)) for t in zones.tables}


def main(argv: list[str] | None = None) -> int:
    from servio_vision.capture import open_source
    from servio_vision.privacy import PrivacyError

    parser = argparse.ArgumentParser(description="Guarda un recorte por mesa.")
    parser.add_argument("--source", required=True, help="video, carpeta, 0 o URL")
    parser.add_argument("--zones", type=Path, default=DEFAULT_ZONES)
    parser.add_argument("--out", type=Path, default=Path("out/crops"))
    parser.add_argument("--interval", type=float, default=30.0)
    parser.add_argument("--limit", type=int, default=3, help="cuántos frames")
    args = parser.parse_args(argv)

    zones = load_zones(args.zones)
    args.out.mkdir(parents=True, exist_ok=True)
    count = 0
    try:
        for frame, ts in open_source(args.source, args.interval):
            for table_id, jpeg in crop_tables(frame, zones).items():
                name = f"{ts:%H%M%S}_{table_id}.jpg"
                (args.out / name).write_bytes(jpeg)
                print(args.out / name)
            count += 1
            if count >= args.limit:
                break
    except (OSError, PrivacyError) as e:
        print(e, file=sys.stderr)
        return 2
    return 0 if count else 1


if __name__ == "__main__":
    sys.exit(main())
