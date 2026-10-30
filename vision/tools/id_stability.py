"""Estabilidad de ids sobre un video: qué % de platos conserva su id entre frames.

Los ids salen solo de YOLO + `tracking.py` (Claude no interviene), así que por
defecto corre solo el detector: rápido y sin gastar uso de Claude. También puede
leer un `linea_de_tiempo.json` de `probar_video.py`.

Uso (desde vision/):
  python tools/id_stability.py "C:/Users/tomas/Downloads/video mesa platos ia.mp4"
  python tools/id_stability.py video.mp4 --cada 2
  python tools/id_stability.py out/video/run1/linea_de_tiempo.json
"""

import argparse
import itertools
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def stability(frames: list[list[str] | None]) -> dict:
    """Métricas de ids a partir de los ids de cada frame, en orden (`None` = lectura
    fallida).

    - `conservan`: de los platos de un frame, qué fracción sigue con el mismo id en
      el siguiente (un plato que desaparece o cambia de id cuenta como perdido).
    - `ids_nuevos`: ids que aparecen después del primer frame.
    - `ids_distintos` vs. `max_platos`: si son iguales, ningún plato cambió de id.
    """
    kept = total = 0
    for a, b in itertools.pairwise(frames):
        if a is None or b is None:  # lectura fallida: no compara a través de ella
            continue
        total += len(a)
        kept += len(set(a) & set(b))
    read = [ids for ids in frames if ids is not None]
    seen = set(read[0]) if read else set()
    new = 0
    for ids in read[1:]:
        new += len(set(ids) - seen)
        seen |= set(ids)
    counts = [len(ids) for ids in read]
    return {
        "frames": len(frames),
        "platos_por_frame": statistics.median(counts) if counts else 0,
        "max_platos": max(counts, default=0),
        "ids_distintos": len(seen),
        "ids_nuevos": new,
        "conservan": round(kept / total, 3) if total else None,
    }


def ids_from_timeline(path: Path) -> list[list[str] | None]:
    timeline = json.loads(path.read_text(encoding="utf-8"))
    return [
        [i["id"] for i in f["report"]["items"]] if f.get("report") else None
        for f in timeline
    ]


def ids_from_video(source: str, every_s: float, max_frames: int) -> list[list[str]]:
    import cv2
    from PIL import Image

    from servio_vision.capture import open_source
    from servio_vision.classifiers.yolo import find_plates
    from servio_vision.detector import PlateDetector
    from servio_vision.schemas import Item
    from servio_vision.tracking import IdCounter, position_label

    detector, counter = PlateDetector(), IdCounter()
    previous: list[Item] = []
    frames = []
    for n, (frame, _) in enumerate(open_source(source, interval=every_s), 1):
        img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        plates = find_plates(detector, img, previous, counter, "T1")
        # sin estado: solo importan id y caja para emparejar el próximo frame
        previous = [
            Item(id=p.id, position=position_label(p.bbox), bbox=p.bbox, visible=False)
            for p in plates
        ]
        ids = [p.id for p in plates]
        frames.append(ids)
        print(f"f_{n:03d}: {len(ids)} platos {' '.join(sorted(ids, key=_num))}")
        if max_frames and n >= max_frames:
            break
    return frames


def _num(pid: str) -> int:
    return int(pid[1:]) if pid[1:].isdigit() else 0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("fuente", help="video, carpeta de fotos o linea_de_tiempo.json")
    ap.add_argument("--cada", type=float, default=1.0, help="segundos entre frames")
    ap.add_argument("--max-frames", type=int, default=0)
    a = ap.parse_args()

    if a.fuente.endswith(".json"):
        frames = ids_from_timeline(Path(a.fuente))
    else:
        from servio_vision.privacy import PrivacyError

        try:
            frames = ids_from_video(a.fuente, a.cada, a.max_frames)
        except PrivacyError as e:  # sin el detector de caras no se lee un frame (C-3)
            raise SystemExit(f"{e}") from e
    m = stability(frames)
    print(json.dumps(m, indent=2, ensure_ascii=False))
    if m["conservan"] is not None:
        print(
            f"\n{round(100 * m['conservan'])} % de los platos conserva su id de un frame"
            f" al siguiente ({m['ids_distintos']} ids para {m['max_platos']} platos)."
        )


if __name__ == "__main__":
    main()
