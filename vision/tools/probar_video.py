"""Corre un clasificador sobre un video y deja la línea de tiempo de cada plato.

Saca un frame cada `--cada` segundos, lo clasifica como una sola mesa (el frame
entero) pasándole los platos de la lectura anterior, y guarda cada frame dibujado
(`f_NNN.jpg`), una hoja con el recorte de cada plato y su clasificación
(`f_NNN_platos.jpg`) y `linea_de_tiempo.json`. Sirve para TOM-7/TOM-8 antes de tener el loop de Jessica.

Uso (desde vision/):
  python tools/probar_video.py "C:/Users/tomas/Downloads/video mesa platos ia.mp4"
  python tools/probar_video.py video.mp4 --cada 2 --max-frames 5 --classifier hybrid
"""

import argparse
import io
import json
import logging
import sys
import time
from pathlib import Path

import cv2
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from servio_vision.classifier import get_classifier

COLORS = {
    "lleno": "#2e7d32",
    "comiendo": "#f9a825",
    "por_terminar": "#ef6c00",
    "vacio": "#c62828",
}


def frames(path: str, every_s: float, max_frames: int):
    """(segundo, imagen PIL) cada `every_s` segundos."""
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise SystemExit(f"No pude abrir el video: {path}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    step = max(1, round(fps * every_s))
    for n, idx in enumerate(range(0, total, step), 1):
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ok, frame = cap.read()
        if not ok:
            break
        yield idx / fps, Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        if max_frames and n >= max_frames:
            break
    cap.release()


def _font(size: int):
    for name in ("arialbd.ttf", "DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    return ImageFont.load_default()


def label(it) -> str:
    if not it.visible:
        return f"{it.id} tapado"
    if it.fill_ratio is not None:
        return f"{it.id} {it.state} {round(100 * it.fill_ratio)}%"
    return f"{it.id} {it.state}"


def plate_sheet(img: Image.Image, items, path: Path, side: int = 300) -> None:
    """Hoja con el recorte de cada plato (10 % de margen) y su clasificación abajo."""
    items = [it for it in items if it.bbox is not None]
    if not items:
        return
    cols = min(4, len(items))
    rows = -(-len(items) // cols)
    bar = 56
    sheet = Image.new("RGB", (cols * side, rows * (side + bar)), "white")
    d = ImageDraw.Draw(sheet)
    big, small = _font(20), _font(16)
    w, h = img.size
    for k, it in enumerate(
        sorted(items, key=lambda i: int(i.id[1:]) if i.id[1:].isdigit() else 0)
    ):
        x, y, bw, bh = it.bbox
        m = 0.10
        crop = img.crop(
            (
                max(0, (x - m * bw) * w),
                max(0, (y - m * bh) * h),
                min(w, (x + bw * (1 + m)) * w),
                min(h, (y + bh * (1 + m)) * h),
            )
        )
        crop.thumbnail((side, side))
        cx, cy = (k % cols) * side, (k // cols) * (side + bar)
        sheet.paste(
            crop, (cx + (side - crop.width) // 2, cy + (side - crop.height) // 2)
        )
        color = COLORS.get(it.state, "#1565c0") if it.visible else "#616161"
        d.rectangle((cx, cy + side, cx + side - 1, cy + side + bar - 1), fill=color)
        d.text((cx + 8, cy + side + 4), label(it), fill="white", font=big)
        conf = "" if it.confidence is None else f"confianza {it.confidence:.2f}"
        d.text((cx + 8, cy + side + 32), conf, fill="white", font=small)
    sheet.save(path, quality=85)


def draw(img: Image.Image, items, path: Path) -> None:
    img = img.copy()
    d = ImageDraw.Draw(img)
    font = _font(max(18, img.width // 70))
    w, h = img.size
    for it in items:
        if it.bbox is None:
            continue
        x, y, bw, bh = it.bbox
        box = (x * w, y * h, (x + bw) * w, (y + bh) * h)
        color = COLORS.get(it.state, "#1565c0")
        text = label(it)
        d.rectangle(box, outline=color, width=5)
        tb = d.textbbox(box[:2], text, font=font)
        d.rectangle((tb[0] - 4, tb[1] - 4, tb[2] + 4, tb[3] + 4), fill=color)
        d.text(box[:2], text, fill="white", font=font)
    img.save(path, quality=85)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--cada", type=float, default=1.0, help="segundos entre frames")
    ap.add_argument("--max-frames", type=int, default=0)
    ap.add_argument("--classifier", default="hybrid")
    ap.add_argument("--out", default="out/video")
    a = ap.parse_args()

    logging.basicConfig(level=logging.WARNING)
    classifier = get_classifier(a.classifier)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    previous, timeline = [], []
    for n, (sec, img) in enumerate(frames(a.video, a.cada, a.max_frames), 1):
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=85)
        start = time.monotonic()
        report = classifier.classify(buf.getvalue(), "T1", previous)
        took = time.monotonic() - start
        name = f"f_{n:03d}"
        if report is None:
            print(f"{name} t={sec:5.1f}s ({took:4.1f}s): lectura fallida", flush=True)
            timeline.append({"frame": name, "segundo": round(sec, 1), "report": None})
            img.save(out / f"{name}.jpg", quality=85)
            continue
        previous = report.items
        draw(img, report.items, out / f"{name}.jpg")
        plate_sheet(img, report.items, out / f"{name}_platos.jpg")
        timeline.append(
            {"frame": name, "segundo": round(sec, 1), "report": report.model_dump()}
        )
        summary = ", ".join(
            f"{i.id}={i.state if i.visible else 'tapado'}" for i in report.items
        )
        print(
            f"{name} t={sec:5.1f}s ({took:4.1f}s): {summary or 'sin platos'}",
            flush=True,
        )

    with open(out / "linea_de_tiempo.json", "w", encoding="utf-8") as f:
        json.dump(timeline, f, indent=2, ensure_ascii=False)
    print(f"Listo: {len(timeline)} frames en {out}")


if __name__ == "__main__":
    main()
