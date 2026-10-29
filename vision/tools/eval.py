"""Evaluación de estado (TOM-6): acierto de Claude sobre recortes de plato etiquetados.

El set es una carpeta con `labels.csv` (`archivo,estado[,nota]`) y las imágenes de
cada plato. Los recortes se mandan a Claude de a `--por-llamada` (como los platos
de una mesa), con una hoja de todos ellos como imagen de la mesa.

Uso (desde vision/):
  python tools/eval.py --set data/eval
  python tools/eval.py --set data/eval --por-llamada 6 --max 30

Las imágenes no van al repo (ver 09-convenciones.md). El set del equipo está en
la carpeta compartida del proyecto, `servio-eval/`.
"""

import argparse
import csv
import io
import json
import logging
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image

from servio_vision.classifiers.claude_plates import estimate_plates
from servio_vision.schemas import state_from_fill_ratio

STATES = ["lleno", "comiendo", "por_terminar", "vacio"]
CROP_SIDE = 384


def jpeg(img: Image.Image, side: int) -> bytes:
    img = img.copy()
    img.thumbnail((side, side))
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=85)
    return buf.getvalue()


def table_sheet(imgs: list[Image.Image], side: int = 256) -> Image.Image:
    """Hoja con los platos en grilla, en el lugar de la foto de la mesa."""
    cols = min(4, len(imgs))
    rows = -(-len(imgs) // cols)
    sheet = Image.new("RGB", (cols * side, rows * side), (120, 90, 60))
    for k, img in enumerate(imgs):
        img = img.copy()
        img.thumbnail((side, side))
        sheet.paste(img, ((k % cols) * side, (k // cols) * side))
    return sheet


def summarize(results: list[dict]) -> dict:
    """Acierto exacto, a un estado de distancia, matriz y fill_ratio medio por clase."""
    rated = [r for r in results if r["predicho"] is not None]
    exact = sum(r["predicho"] == r["estado"] for r in rated)
    near = sum(
        abs(STATES.index(r["predicho"]) - STATES.index(r["estado"])) <= 1 for r in rated
    )
    matrix = {
        real: dict(Counter(r["predicho"] for r in rated if r["estado"] == real))
        for real in STATES
        if any(r["estado"] == real for r in rated)
    }
    fill = {
        real: round(sum(vals) / len(vals), 2)
        for real in STATES
        if (vals := [r["fill_ratio"] for r in rated if r["estado"] == real])
    }
    return {
        "platos": len(results),
        "sin_respuesta": len(results) - len(rated),
        "acierto": round(exact / len(rated), 3) if rated else None,
        "a_un_estado": round(near / len(rated), 3) if rated else None,
        "matriz": matrix,
        "fill_ratio_medio": fill,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", required=True, help="carpeta con labels.csv")
    ap.add_argument("--por-llamada", type=int, default=8)
    ap.add_argument("--max", type=int, default=0, help="evaluar solo los primeros N")
    ap.add_argument("--out", default="out/eval")
    a = ap.parse_args()

    logging.basicConfig(level=logging.WARNING)
    folder = Path(a.set)
    with open(folder / "labels.csv", encoding="utf-8") as f:
        labels = [r for r in csv.DictReader(f) if r["estado"] in STATES]
    if a.max:
        labels = labels[: a.max]

    results, latencies = [], []
    for start in range(0, len(labels), a.por_llamada):
        batch = labels[start : start + a.por_llamada]
        imgs = [Image.open(folder / r["archivo"]) for r in batch]
        t0 = time.monotonic()
        report = estimate_plates(
            [jpeg(i, CROP_SIDE) for i in imgs], f"EVAL{start // a.por_llamada + 1}"
        )
        latencies.append(time.monotonic() - t0)
        for r, est in zip(batch, report.plates if report else [None] * len(batch)):
            visible = est is not None and est.visible
            results.append(
                {
                    "archivo": r["archivo"],
                    "estado": r["estado"],
                    "fill_ratio": est.fill_ratio if visible else None,
                    "predicho": state_from_fill_ratio(est.fill_ratio)
                    if visible
                    else None,
                }
            )
        done = len(results)
        print(f"{done}/{len(labels)} platos ({latencies[-1]:.1f} s)", flush=True)

    summary = summarize(results)
    summary["latencia_media_s"] = round(sum(latencies) / len(latencies), 1)
    summary["platos_por_llamada"] = a.por_llamada
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "resultados.json", "w", encoding="utf-8") as f:
        json.dump(
            {"resumen": summary, "platos": results}, f, indent=2, ensure_ascii=False
        )
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"Detalle por plato en {out / 'resultados.json'}")


if __name__ == "__main__":
    main()
