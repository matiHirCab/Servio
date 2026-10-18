"""Métricas del negocio: platos con problemas y mozos, para el cierre del pitch.

Lee el historial (`fixtures/restaurante/historial.jsonl`, simulado con
`tools/generar_historial.py`) y le suma las respuestas reales de los mozos que haya
en el log de la corrida (`out/eventos.jsonl`, eventos `alert_feedback`). Muestra:
- platos que vuelven llenos: % de las veces que se sirvieron, con el motivo más
  común que respondieron los mozos;
- mozos: minutos hasta atender una mesa que terminó, y segundos en responder el aviso.

    python tools/metricas.py [--image out/metricas.png]
"""

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

VISION_DIR = Path(__file__).resolve().parents[1]
HISTORY = VISION_DIR.parent / "fixtures" / "restaurante" / "historial.jsonl"
LIVE_LOG = VISION_DIR / "out" / "eventos.jsonl"

# Tema oscuro (paleta de referencia de la guía de visualización): una serie por
# gráfico → un solo color; el texto nunca va con el color de la barra.
SURFACE = (26, 26, 25)
TEXT = (240, 239, 236)
TEXT_2 = (195, 194, 183)
MUTED = (90, 90, 86)
BAR = (57, 135, 229)


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    return rows


def compute(history: list[dict], live: list[dict]) -> dict:
    served: Counter = Counter()
    returned: Counter = Counter()
    reasons: dict[str, Counter] = defaultdict(Counter)
    attend: dict[str, list[float]] = defaultdict(list)
    answer: dict[str, list[float]] = defaultdict(list)
    for r in history:
        if r["tipo"] == "servido":
            served[r["plato"]] += 1
        elif r["tipo"] == "plato_lleno":
            returned[r["plato"]] += 1
            reasons[r["plato"]][r["respuesta"]] += 1
            answer[r["mozo"]].append(r["segundos_respuesta"])
        elif r["tipo"] == "mesa_terminada":
            attend[r["mozo"]].append(r["minutos_hasta_atender"])
    live_answers = 0
    for e in live:
        if e.get("event") == "alert_feedback" and e.get("dish"):
            live_answers += 1
            reasons[e["dish"]][e["answer"]] += 1
            if e.get("waiter"):
                answer[e["waiter"]].append(e["seconds_to_answer"])
    dishes = sorted(
        (
            (
                dish,
                returned[dish] / n,
                reasons[dish].most_common(1)[0][0] if reasons[dish] else "-",
            )
            for dish, n in served.items()
            if n
        ),
        key=lambda d: d[1],
        reverse=True,
    )
    mean = lambda v: sum(v) / len(v)
    waiters_attend = sorted(
        ((w, mean(v)) for w, v in attend.items()), key=lambda x: -x[1]
    )
    waiters_answer = sorted(
        ((w, mean(v)) for w, v in answer.items()), key=lambda x: -x[1]
    )
    days = len({r["fecha"] for r in history})
    return {
        "dishes": dishes,
        "attend": waiters_attend,
        "answer": waiters_answer,
        "days": days,
        "alerts": sum(returned.values()) + live_answers,
        "live_answers": live_answers,
    }


def print_report(m: dict) -> None:
    print(f"Servio · últimos {m['days']} días · {m['alerts']} avisos de plato lleno")
    print("\nPlatos que vuelven llenos (% de las veces que se sirvieron)")
    for dish, rate, reason in m["dishes"]:
        print(f"  {dish:34} {rate * 100:5.1f} %   motivo más común: {reason}")
    print("\nMinutos hasta atender una mesa que terminó")
    for waiter, v in m["attend"]:
        print(f"  {waiter:10} {v:5.1f} min")
    print("\nSegundos en responder el aviso")
    for waiter, v in m["answer"]:
        print(f"  {waiter:10} {v:5.0f} s")
    if m["live_answers"]:
        print(f"\n(incluye {m['live_answers']} respuestas reales de la última corrida)")


def _fonts(size: int, bold_size: int):
    from PIL import ImageFont

    for regular, bold in (
        ("arial.ttf", "arialbd.ttf"),
        ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf"),
    ):
        try:
            return ImageFont.truetype(regular, size), ImageFont.truetype(
                bold, bold_size
            )
        except OSError:
            continue
    return ImageFont.load_default(size=size), ImageFont.load_default(size=bold_size)


def _bars(draw, x, y, w, title, rows, fmt, fonts, label_w):
    """Barras horizontales: nombre a la izquierda, valor en la punta (texto, no color)."""
    font, bold = fonts
    draw.text((x, y), title, font=bold, fill=TEXT)
    y += 56
    top = max(v for _, v, *_ in rows) or 1
    bar_x = x + label_w
    bar_w = w - label_w - 300
    for name, value, *extra in rows:
        draw.text((x, y), name, font=font, fill=TEXT_2)
        length = max(6, int(bar_w * value / top))
        # 22 px de grueso, punta redondeada y base recta (guía de visualización)
        draw.rounded_rectangle(
            [bar_x, y + 4, bar_x + length, y + 26], radius=4, fill=BAR
        )
        draw.rectangle([bar_x, y + 4, bar_x + 4, y + 26], fill=BAR)
        draw.text((bar_x + length + 12, y), fmt(value, *extra), font=font, fill=TEXT)
        y += 44
    draw.line([bar_x, y - 44 * len(rows) - 2, bar_x, y - 14], fill=MUTED, width=1)
    return y


def render(m: dict, path: Path) -> None:
    from PIL import Image, ImageDraw

    width = 1800
    dishes = m["dishes"][:6]
    height = (
        190
        + 56
        + 44 * len(dishes)
        + 40
        + 56
        + 44 * max(len(m["attend"]), len(m["answer"]))
        + 30
    )
    image = Image.new("RGB", (width, height), SURFACE)
    draw = ImageDraw.Draw(image)
    fonts = _fonts(26, 32)
    big = _fonts(28, 46)[1]
    draw.text((60, 50), "Servio: lo que aprendimos del salón", font=big, fill=TEXT)
    sub = f"Últimos {m['days']} días · {m['alerts']} avisos de plato lleno con respuesta del mozo"
    draw.text((60, 115), sub, font=fonts[0], fill=TEXT_2)

    y = _bars(
        draw,
        60,
        190,
        width - 120,
        "Platos que vuelven llenos",
        dishes,
        lambda v, reason: f"{v * 100:.0f} %   ·   {reason}",
        fonts,
        460,
    )
    half = (width - 180) // 2
    y += 40
    _bars(
        draw,
        60,
        y,
        half,
        "Minutos hasta atender una mesa que terminó",
        m["attend"],
        lambda v: f"{v:.1f} min",
        fonts,
        160,
    )
    _bars(
        draw,
        60 + half + 60,
        y,
        half,
        "Segundos en responder el aviso",
        m["answer"],
        lambda v: f"{v:.0f} s",
        fonts,
        160,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Métricas de platos y mozos.")
    parser.add_argument("--historial", type=Path, default=HISTORY)
    parser.add_argument("--corrida", type=Path, default=LIVE_LOG)
    parser.add_argument("--image", type=Path, help="guardar también como imagen")
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    history = read_jsonl(args.historial)
    if not history:
        print(f"No hay historial en {args.historial}: corré tools/generar_historial.py")
        return 2
    m = compute(history, read_jsonl(args.corrida))
    print_report(m)
    if args.image:
        render(m, args.image)
        print(f"\nImagen guardada en {args.image}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
