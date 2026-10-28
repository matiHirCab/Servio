"""Marcar las mesas con clics y guardar config/zones.json (JES-4).

Herramienta de desarrollo y respaldo: en la demo las mesas las detecta la IA
(Tomás), que escribe el mismo config/zones.json. Esto sirve para avanzar
mientras tanto, o si la detección falla.

    python tools/draw_zones.py --source <video | foto | carpeta | 0 | url> [--at 10]

Teclas en la ventana:
    clic izquierdo   agrega un punto a la mesa que estás marcando
    Enter / espacio  cierra la mesa (mínimo 3 puntos) → Mesa 1, Mesa 2…
    u                deshace el último punto
    d                borra la última mesa cerrada
    s                guarda y sale
    q / Esc          sale sin guardar
"""

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from servio_vision import privacy
from servio_vision.capture import open_source
from servio_vision.zones import (
    DEFAULT_ZONES,
    TableZone,
    Zones,
    save_zones,
)

WINDOW = "Servio: marcar mesas"
DONE_COLOR = (0, 200, 0)
DRAFT_COLOR = (0, 200, 255)
MAX_WINDOW_WIDTH = 1600


class ZoneEditor:
    """Estado de la edición, sin nada de interfaz (se puede testear)."""

    def __init__(self, frame_size: tuple[int, int], camera_id: str = "cam-1"):
        self.frame_size = frame_size
        self.camera_id = camera_id
        self.tables: list[TableZone] = []
        self.draft: list[tuple[int, int]] = []

    def add_point(self, x: int, y: int) -> None:
        width, height = self.frame_size
        self.draft.append((min(max(x, 0), width - 1), min(max(y, 0), height - 1)))

    def undo_point(self) -> None:
        if self.draft:
            self.draft.pop()

    def close_table(self) -> TableZone | None:
        if len(self.draft) < 3:
            return None
        n = self._next_number()
        table = TableZone(table_id=f"T{n}", name=f"Mesa {n}", polygon=self.draft)
        self.tables.append(table)
        self.draft = []
        return table

    def delete_last_table(self) -> None:
        if self.tables:
            self.tables.pop()

    def to_zones(self) -> Zones:
        return Zones(
            camera_id=self.camera_id, frame_size=self.frame_size, tables=self.tables
        )

    def _next_number(self) -> int:
        used = {t.table_id for t in self.tables}
        n = 1
        while f"T{n}" in used:
            n += 1
        return n


def render(frame: np.ndarray, editor: ZoneEditor) -> np.ndarray:
    view = frame.copy()
    for t in editor.tables:
        points = np.array(t.polygon, np.int32)
        cv2.polylines(view, [points], True, DONE_COLOR, 3)
        x, y = t.polygon[0]
        cv2.putText(
            view, t.name, (x + 8, y + 32), cv2.FONT_HERSHEY_SIMPLEX, 1, DONE_COLOR, 2
        )
    if editor.draft:
        points = np.array(editor.draft, np.int32)
        cv2.polylines(view, [points], False, DRAFT_COLOR, 2)
        for p in editor.draft:
            cv2.circle(view, p, 5, DRAFT_COLOR, -1)
    help_text = "clic: punto | Enter: cerrar mesa | u: deshacer | d: borrar mesa | s: guardar | q: salir"
    cv2.putText(
        view, help_text, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2
    )
    return view


def first_frame(source: str, at: float) -> np.ndarray:
    # también una foto suelta: open_source la lee (y difumina las caras, C-3)
    for i, (frame, _) in enumerate(open_source(source, interval=at or 1.0)):
        if at == 0 or i == 1:
            return frame
    raise OSError(f"La fuente {source} no tiene un frame en el segundo {at}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Marcar las mesas con clics.")
    parser.add_argument("--source", required=True)
    parser.add_argument("--at", type=float, default=0, help="segundo del video a usar")
    parser.add_argument("--out", type=Path, default=DEFAULT_ZONES)
    args = parser.parse_args(argv)

    try:
        frame = first_frame(args.source, args.at)
    except (OSError, privacy.PrivacyError) as e:
        print(e, file=sys.stderr)
        return 2
    height, width = frame.shape[:2]
    editor = ZoneEditor((width, height))

    scale = min(1.0, MAX_WINDOW_WIDTH / width)  # la ventana entra en pantalla

    def on_mouse(event, x, y, flags, param):
        if event == cv2.EVENT_LBUTTONDOWN:
            editor.add_point(round(x / scale), round(y / scale))

    cv2.namedWindow(WINDOW, cv2.WINDOW_AUTOSIZE)
    cv2.setMouseCallback(WINDOW, on_mouse)
    while True:
        view = render(frame, editor)
        if scale < 1:
            view = cv2.resize(view, None, fx=scale, fy=scale)
        cv2.imshow(WINDOW, view)
        key = cv2.waitKey(30) & 0xFF
        if key in (13, 10, 32):
            editor.close_table()
        elif key == ord("u"):
            editor.undo_point()
        elif key == ord("d"):
            editor.delete_last_table()
        elif key == ord("s"):
            if not editor.tables:
                print("No hay mesas para guardar.")
                continue
            save_zones(editor.to_zones(), args.out)
            print(f"Guardadas {len(editor.tables)} mesas en {args.out}")
            break
        elif key in (ord("q"), 27):
            print("Salí sin guardar.")
            break
        if cv2.getWindowProperty(WINDOW, cv2.WND_PROP_VISIBLE) < 1:
            break
    cv2.destroyAllWindows()
    return 0


if __name__ == "__main__":
    sys.exit(main())
