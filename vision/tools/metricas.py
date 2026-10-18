"""Métricas del negocio: platos con problemas y mozos, para el cierre del pitch.

Todo sale de la base relacional (`vision/out/servio.db`, ver `servio_vision/db.py`)
con consultas SQL: lo que pasó en vivo (avisos, respuestas de los mozos) más el
historial simulado (`fixtures/restaurante/historial.jsonl`, que se importa la
primera vez). Muestra:
- platos que vuelven llenos: % de las veces que se sirvieron, con el motivo más
  común que respondieron los mozos;
- mozos: minutos hasta atender una mesa que terminó, y segundos en responder el aviso.

    python tools/metricas.py [--image out/metricas.png] [--sql]
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from servio_vision.db import Database
from servio_vision.restaurant import Restaurant

VISION_DIR = Path(__file__).resolve().parents[1]
HISTORY = VISION_DIR.parent / "fixtures" / "restaurante" / "historial.jsonl"
DB = VISION_DIR / "out" / "servio.db"

# Tema oscuro (paleta de referencia de la guía de visualización): una serie por
# gráfico → un solo color; el texto nunca va con el color de la barra.
SURFACE = (26, 26, 25)
TEXT = (240, 239, 236)
TEXT_2 = (195, 194, 183)
MUTED = (90, 90, 86)
BAR = (57, 135, 229)


# Las consultas, a la vista: es lo que se puede mostrar en el pitch (--sql).
SQL_DISHES = """
SELECT p.nombre AS plato,
       COUNT(DISTINCT a.id) * 1.0 / COUNT(DISTINCT ps.id) AS vuelve_lleno,
       (SELECT r.texto
          FROM respuestas r
          JOIN avisos a2 ON a2.id = r.aviso_id
          JOIN platos_servidos ps2 ON ps2.id = a2.plato_servido_id
         WHERE ps2.plato_id = p.id
         GROUP BY r.texto ORDER BY COUNT(*) DESC LIMIT 1) AS motivo
  FROM platos p
  JOIN platos_servidos ps ON ps.plato_id = p.id
  LEFT JOIN avisos a ON a.plato_servido_id = ps.id AND a.tipo = 'plato_lleno_mucho'
 GROUP BY p.id
 ORDER BY vuelve_lleno DESC
"""
SQL_ATTEND = """
SELECT m.nombre AS mozo,
       AVG((julianday(a.resuelto_at) - julianday(a.abierto_at)) * 24 * 60) AS minutos
  FROM avisos a JOIN mozos m ON m.id = a.mozo_id
 WHERE a.tipo = 'mesa_terminada' AND a.resuelto_at IS NOT NULL
 GROUP BY m.id
 ORDER BY minutos DESC
"""
SQL_ANSWER = """
SELECT m.nombre AS mozo, AVG(r.segundos) AS segundos
  FROM respuestas r JOIN mozos m ON m.id = r.mozo_id
 GROUP BY m.id
 ORDER BY segundos DESC
"""
SQL_TOTALS = """
SELECT (SELECT COUNT(DISTINCT substr(abierto_at, 1, 10)) FROM avisos
         WHERE origen = 'historial') AS dias,
       (SELECT COUNT(*) FROM respuestas) AS respuestas,
       (SELECT COUNT(*) FROM respuestas r JOIN avisos a ON a.id = r.aviso_id
         WHERE a.origen = 'camara') AS en_vivo
"""


def compute(db: Database) -> dict:
    totals = db.query(SQL_TOTALS)[0]
    return {
        "dishes": [
            (r["plato"], r["vuelve_lleno"], r["motivo"] or "-")
            for r in db.query(SQL_DISHES)
        ],
        "attend": [(r["mozo"], r["minutos"]) for r in db.query(SQL_ATTEND)],
        "answer": [(r["mozo"], r["segundos"]) for r in db.query(SQL_ANSWER)],
        "days": totals["dias"],
        "alerts": totals["respuestas"],
        "live_answers": totals["en_vivo"],
    }


def print_report(m: dict) -> None:
    print(f"Servio · últimos {m['days']} días · {m['alerts']} respuestas de mozos")
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
        print(f"\n(incluye {m['live_answers']} respuestas reales de las demos)")


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
    sub = (
        f"Últimos {m['days']} días · {m['alerts']} respuestas de los mozos a los avisos"
    )
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
    parser.add_argument("--db", type=Path, default=DB)
    parser.add_argument("--historial", type=Path, default=HISTORY)
    parser.add_argument("--image", type=Path, help="guardar también como imagen")
    parser.add_argument("--sql", action="store_true", help="mostrar las consultas")
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    db = Database(args.db)
    restaurant = Restaurant.load()
    db.load_restaurant(restaurant)
    if args.historial.exists():
        imported = db.import_history(args.historial, restaurant)
        if imported:
            print(f"(historial importado a la base: {imported} avisos)\n")
    if args.sql:
        for title, sql in (
            ("Platos que vuelven llenos", SQL_DISHES),
            ("Minutos hasta atender", SQL_ATTEND),
            ("Segundos en responder", SQL_ANSWER),
        ):
            print(f"-- {title}{sql}")
    m = compute(db)
    db.close()
    if not m["dishes"]:
        print("La base no tiene datos: corré tools/generar_historial.py o una demo.")
        return 2
    print_report(m)
    if args.image:
        render(m, args.image)
        print(f"\nImagen guardada en {args.image}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
