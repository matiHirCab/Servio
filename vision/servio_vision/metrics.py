"""Métricas del negocio sacadas de la base (`db.py`): platos que vuelven llenos y
tiempos de los mozos. Las usan `tools/metricas.py` (terminal e imagen) y el comando
`/metricas` del bot de Telegram (`alerts/feedback.py`).
"""

from pathlib import Path

from servio_vision import VISION_DIR
from servio_vision.db import Database

HISTORY = VISION_DIR.parent / "fixtures" / "restaurante" / "historial.jsonl"

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
 WHERE a.tipo = 'mesa_terminada' AND a.motivo_cierre = 'retirados'
 GROUP BY m.id
 ORDER BY minutos DESC
"""
# El último retiro de platos en vivo (cámara): para mostrarlo en la demo.
SQL_LAST_PICKUP = """
SELECT m.nombre AS mozo, me.nombre AS mesa,
       (julianday(a.resuelto_at) - julianday(a.abierto_at)) * 24 * 3600 AS segundos
  FROM avisos a
  JOIN mesas me ON me.id = a.mesa_id
  LEFT JOIN mozos m ON m.id = a.mozo_id
 WHERE a.tipo = 'mesa_terminada' AND a.motivo_cierre = 'retirados'
   AND a.origen = 'camara'
 ORDER BY a.resuelto_at DESC
 LIMIT 1
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


# Por plato, cada problema que respondieron los mozos y su % sobre todas las
# respuestas de ese plato ("Está todo bien" no es un problema, pero cuenta en el total).
SQL_PROBLEMS = """
SELECT p.nombre AS plato, r.texto AS problema,
       COUNT(*) * 1.0 / (SELECT COUNT(*)
                           FROM respuestas r2
                           JOIN avisos a2 ON a2.id = r2.aviso_id
                           JOIN platos_servidos ps2 ON ps2.id = a2.plato_servido_id
                          WHERE ps2.plato_id = p.id) AS porcentaje
  FROM respuestas r
  JOIN avisos a ON a.id = r.aviso_id
  JOIN platos_servidos ps ON ps.id = a.plato_servido_id
  JOIN platos p ON p.id = ps.plato_id
 WHERE r.texto NOT LIKE 'Está todo bien%'
 GROUP BY p.id, r.texto
 ORDER BY p.nombre, porcentaje DESC
"""


def compute(db: Database) -> dict:
    totals = db.query(SQL_TOTALS)[0]
    problems: dict[str, list[tuple[str, float]]] = {}
    for r in db.query(SQL_PROBLEMS):
        problems.setdefault(r["plato"], []).append((r["problema"], r["porcentaje"]))
    return {
        "problems": problems,
        "dishes": [
            (r["plato"], r["vuelve_lleno"], r["motivo"] or "-")
            for r in db.query(SQL_DISHES)
        ],
        "attend": [(r["mozo"], r["minutos"]) for r in db.query(SQL_ATTEND)],
        "last_pickup": next(
            ((r["mozo"], r["mesa"], r["segundos"]) for r in db.query(SQL_LAST_PICKUP)),
            None,
        ),
        "answer": [(r["mozo"], r["segundos"]) for r in db.query(SQL_ANSWER)],
        "days": totals["dias"],
        "alerts": totals["respuestas"],
        "live_answers": totals["en_vivo"],
    }


# Métricas de roles.py que muestra este reporte (los roles filtran por estas).
REPORT_METRICS = ("platos_que_vuelven", "tiempo_respuesta")


def print_report(m: dict, metrics: set[str] | None = None) -> None:
    """Reporte en la terminal. `metrics`: ids de roles.py a mostrar (None = todas)."""
    show = set(REPORT_METRICS) if metrics is None else metrics
    print(f"Servio · últimos {m['days']} días · {m['alerts']} respuestas de mozos")
    if "platos_que_vuelven" in show:
        print("\nPlatos que vuelven llenos (% de las veces que se sirvieron)")
        for dish, rate, reason in m["dishes"]:
            print(f"  {dish:34} {rate * 100:5.1f} %   motivo más común: {reason}")
    if "tiempo_respuesta" in show:
        print("\nMinutos hasta retirar los platos de una mesa que terminó")
        for waiter, v in m["attend"]:
            print(f"  {waiter:10} {v:5.1f} min")
        print("\nSegundos en responder el aviso")
        for waiter, v in m["answer"]:
            print(f"  {waiter:10} {v:5.0f} s")
    if m["problems"] and "platos_que_vuelven" in show:
        print("\nProblemas por plato (% de las respuestas de los mozos)")
        for dish, _, _ in m["dishes"]:
            if dish in m["problems"]:
                print(f"  {dish}")
                for text, pct in problem_lines(m, dish):
                    print(f"    {text:30} {pct * 100:5.1f} %")
    if m["live_answers"]:
        print(f"\n(incluye {m['live_answers']} respuestas reales de las demos)")


def problem_lines(m: dict, dish: str) -> list[tuple[str, float]]:
    """Problemas de un plato, del más al menos común."""
    return sorted(m["problems"].get(dish, []), key=lambda p: -p[1])


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
        "Minutos hasta retirar los platos (mesa terminada)",
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


def problems_text(m: dict) -> str | None:
    """Problemas por plato para Telegram, en un mensaje aparte (máx. 4096
    caracteres): del plato que más vuelve lleno al que menos."""
    with_problems = [d for d in m["dishes"] if d[0] in m["problems"]]
    if not with_problems:
        return None
    lines = ["🔎 Platos con problemas (qué respondieron los mozos)"]
    for dish, rate, _ in with_problems:
        lines.append("")
        lines.append(f"🍽️ {dish} · vuelve lleno el {rate * 100:.0f} %")
        lines += [
            f"   {text}: {pct * 100:.0f} %" for text, pct in problem_lines(m, dish)
        ]
    return "\n".join(lines)[:4096]


def summary(m: dict) -> str:
    """Resumen corto para Telegram (va como pie de la imagen: máx. 1024 caracteres)."""
    lines = ["📊 Servio: lo que aprendimos del salón"]
    worst = [d for d in m["dishes"] if d[1] > 0][:3]
    if worst:
        lines.append("")
        lines.append("Platos que más vuelven llenos:")
        lines += [
            f"• {dish}: {rate * 100:.0f} % ({reason})" for dish, rate, reason in worst
        ]
    if m["last_pickup"]:
        waiter, table, seconds = m["last_pickup"]
        minutes, secs = divmod(round(seconds), 60)
        took = f"{minutes} min {secs} s" if minutes else f"{secs} s"
        lines.append("")
        lines.append(
            f"✅ Hoy: {waiter or 'el mozo'} retiró los platos de la {table} {took} "
            "después de que terminó la mesa"
        )
    if m["attend"]:
        lines.append("")
        lines.append("⏱️ Retiro de platos después de que terminó la mesa (promedio):")
        lines += [f"• {waiter}: {minutes:.1f} min" for waiter, minutes in m["attend"]]
    if m["live_answers"]:
        lines.append(f"📝 Incluye {m['live_answers']} respuestas reales de hoy")
    return "\n".join(lines)[:1024]
