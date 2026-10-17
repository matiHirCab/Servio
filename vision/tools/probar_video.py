"""Corre un clasificador sobre un video y deja la línea de tiempo de cada plato.

Saca un frame cada `--cada` segundos, lo clasifica como una sola mesa (el frame
entero, frames de `servio_vision.capture`) pasándole los platos de la lectura anterior, y guarda cada frame dibujado
(`f_NNN.jpg`), una hoja con el recorte de cada plato y su clasificación
(`f_NNN_platos.jpg`) y `linea_de_tiempo.json`, cada corrida en su carpeta (`out/video/run1`, `run2`, ...). Sirve para TOM-7/TOM-8 antes de tener el loop de Jessica.

Con `hybrid`, las llamadas a Claude de varios frames van en paralelo (`--paralelo`,
4 por defecto): YOLO y los ids siguen en orden, y cada frame se arma recién cuando
terminó el anterior.

Uso (desde vision/):
  python tools/probar_video.py "C:/Users/tomas/Downloads/video mesa platos ia.mp4"
  python tools/probar_video.py video.mp4 --cada 2 --max-frames 5 --classifier hybrid
  python tools/probar_video.py video.mp4 --cada 20 --paralelo 1   # una llamada a la vez
"""

import argparse
import io
import json
import logging
import sys
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from servio_vision.capture import open_source
from servio_vision.classifier import get_classifier

COLORS = {
    "lleno": "#2e7d32",
    "comiendo": "#f9a825",
    "por_terminar": "#ef6c00",
    "vacio": "#c62828",
}


def frames(source: str, every_s: float, max_frames: int):
    """(segundo, imagen PIL) cada `every_s` segundos, con la captura de Jessica."""
    first = None
    for n, (frame, ts) in enumerate(open_source(source, interval=every_s), 1):
        first = first or ts
        img = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        yield (ts - first).total_seconds(), img
        if max_frames and n >= max_frames:
            break


def _font(size: int):
    for name in ("arialbd.ttf", "DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    return ImageFont.load_default()


def label(it) -> str:
    if it.state is None:
        return f"{it.id} tapado"
    if not it.visible:  # tapado ahora: muestra la última lectura
        return f"{it.id} {it.state} (anterior)"
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
        color = COLORS.get(it.state, "#616161")
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


def next_run_dir(base: Path) -> Path:
    """Carpeta nueva para esta corrida: base/run1, base/run2, ..."""
    taken = [
        int(d.name[3:])
        for d in base.glob("run*")
        if d.is_dir() and d.name[3:].isdigit()
    ]
    return base / f"run{max(taken, default=0) + 1}"


def save_frame(n, sec, img, report, took: str, out: Path, timeline: list) -> None:
    name = f"f_{n:03d}"
    if report is None:
        print(f"{name} t={sec:5.1f}s ({took}): lectura fallida", flush=True)
        timeline.append({"frame": name, "segundo": round(sec, 1), "report": None})
        img.save(out / f"{name}.jpg", quality=85)
        return
    draw(img, report.items, out / f"{name}.jpg")
    plate_sheet(img, report.items, out / f"{name}_platos.jpg")
    timeline.append(
        {"frame": name, "segundo": round(sec, 1), "report": report.model_dump()}
    )
    summary = ", ".join(f"{i.id}={i.state or 'tapado'}" for i in report.items)
    print(f"{name} t={sec:5.1f}s ({took}): {summary or 'sin platos'}", flush=True)


def _jpeg_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    return buf.getvalue()


def run_serial(classifier, frame_iter, out: Path, timeline: list) -> None:
    previous = []
    for n, (sec, img) in enumerate(frame_iter, 1):
        t0 = time.monotonic()
        report = classifier.classify(_jpeg_bytes(img), "T1", previous)
        if report is not None:
            previous = report.items
        save_frame(n, sec, img, report, f"{time.monotonic() - t0:4.1f}s", out, timeline)


def run_parallel(classifier, frame_iter, out: Path, timeline: list, workers: int):
    """YOLO en orden en este hilo; Claude en `workers` hilos; el armado de cada
    frame en orden, porque usa la lectura del frame anterior."""

    def ask(prepared):
        t0 = time.monotonic()
        return classifier.ask(prepared), time.monotonic() - t0

    previous = []
    pending: deque = deque()

    def finish_oldest():
        nonlocal previous
        n, sec, img, prepared, future, yolo_s = pending.popleft()
        answer, claude_s = future.result()
        report = classifier.finish(prepared, answer, previous)
        if report is not None:
            previous = report.items
        took = f"yolo {yolo_s:.1f}s, claude {claude_s:.1f}s"
        save_frame(n, sec, img, report, took, out, timeline)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for n, (sec, img) in enumerate(frame_iter, 1):
            t0 = time.monotonic()
            prepared = classifier.prepare(_jpeg_bytes(img), "T1")
            yolo_s = time.monotonic() - t0
            pending.append((n, sec, img, prepared, pool.submit(ask, prepared), yolo_s))
            while len(pending) > workers:
                finish_oldest()
        while pending:
            finish_oldest()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--cada", type=float, default=1.0, help="segundos entre frames")
    ap.add_argument("--max-frames", type=int, default=0)
    ap.add_argument("--classifier", default="hybrid")
    ap.add_argument("--out", default="out/video")
    ap.add_argument(
        "--paralelo", type=int, default=4, help="llamadas a Claude a la vez (hybrid)"
    )
    a = ap.parse_args()

    logging.basicConfig(level=logging.WARNING)
    classifier = get_classifier(a.classifier)
    out = next_run_dir(Path(a.out))
    out.mkdir(parents=True)
    print(f"Guardando en {out}", flush=True)

    timeline: list[dict] = []
    start = time.monotonic()
    if hasattr(classifier, "prepare") and a.paralelo > 1:
        run_parallel(
            classifier, frames(a.video, a.cada, a.max_frames), out, timeline, a.paralelo
        )
    else:
        run_serial(classifier, frames(a.video, a.cada, a.max_frames), out, timeline)
    print(f"Tiempo total: {time.monotonic() - start:.0f} s")

    with open(out / "linea_de_tiempo.json", "w", encoding="utf-8") as f:
        json.dump(timeline, f, indent=2, ensure_ascii=False)
    print(f"Listo: {len(timeline)} frames en {out}")


if __name__ == "__main__":
    main()
