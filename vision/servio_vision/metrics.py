"""Métricas del negocio sacadas de la base (`db.py`): platos que vuelven llenos,
tiempos de los mozos, mesas terminadas sin atender, mesas olvidadas, rotación,
desperdicio en $, carga por mozo, horas pico, ritmo y sobra por plato, adopción
y espera del siguiente plato. Las usan `tools/metricas.py` (terminal e imagen) y el comando
`/metricas` del bot de Telegram (`alerts/feedback.py`).

Qué es cada una, cómo se calcula y quién la ve: docs/context/13-metricas.md.
"""

from datetime import datetime
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


# Umbrales de las métricas de operación (M-1). Son el corte de "mal atendido":
# si cambian, cambian los números que se le muestran al dueño.
UNATTENDED_LIMIT_MIN = 5  # mesa terminada esperando más que esto = mal
FORGOTTEN_LIMIT_S = 120  # aviso de plato lleno sin respuesta en esto = olvidada
LUNCH_UNTIL_HOUR = 17  # antes de esta hora es almuerzo, después cena
PEAK_SLOT_MIN = 30  # franja de las horas pico
GONE_AFTER_MIN = 2  # plato que no se ve hace esto (y la cámara sigue) = se lo llevaron

# Mesa terminada sin atender: minutos desde que todos terminaron hasta que se
# retiraron los platos (solo cierres por retiro, no porque llegó otro plato).
SQL_UNATTENDED = """
SELECT COUNT(*) AS mesas,
       AVG(minutos) AS promedio,
       AVG(minutos > ?) AS fuera_de_tiempo,
       MAX(minutos) AS peor
  FROM (SELECT (julianday(resuelto_at) - julianday(abierto_at)) * 24 * 60 AS minutos
          FROM avisos
         WHERE tipo = 'mesa_terminada' AND motivo_cierre = 'retirados')
"""
# Mesas olvidadas: avisos de plato lleno que nadie respondió, o que tardaron más
# de FORGOTTEN_LIMIT_S en responder. Un aviso abierto todavía sin respuesta no
# cuenta (puede que el mozo esté yendo).
SQL_FORGOTTEN = """
SELECT COALESCE(m.nombre, '(sin mozo)') AS mozo,
       COUNT(*) AS avisos,
       SUM(r.segundos IS NULL OR r.segundos > ?) AS olvidadas
  FROM avisos a
  LEFT JOIN mozos m ON m.id = a.mozo_id
  LEFT JOIN (SELECT aviso_id, MIN(segundos) AS segundos
               FROM respuestas GROUP BY aviso_id) r ON r.aviso_id = a.id
 WHERE a.tipo = 'plato_lleno_mucho'
   AND (a.resuelto_at IS NOT NULL OR r.segundos IS NOT NULL)
 GROUP BY a.mozo_id
 ORDER BY olvidadas * 1.0 / COUNT(*) DESC
"""
# Desperdicio en $: platos que volvieron con comida por un problema (el mozo
# respondió algo distinto de "Está todo bien…"), al precio de la carta. Es una
# cota: cuenta el plato entero aunque haya vuelto a medias.
SQL_WASTE = """
SELECT p.nombre AS plato, COUNT(*) AS platos, SUM(p.precio) AS pesos
  FROM platos_servidos ps
  JOIN platos p ON p.id = ps.plato_id
 WHERE p.precio IS NOT NULL
   AND EXISTS (SELECT 1
                 FROM avisos a JOIN respuestas r ON r.aviso_id = a.id
                WHERE a.plato_servido_id = ps.id
                  AND a.tipo = 'plato_lleno_mucho'
                  AND r.texto NOT LIKE 'Está todo bien%')
 GROUP BY p.id
 ORDER BY pesos DESC
"""
# Ritmo por plato: minutos desde que se sirvió hasta la primera lectura en vacío.
SQL_PACE = """
SELECT p.nombre AS plato, COUNT(*) AS platos,
       AVG((julianday(v.desde) - julianday(ps.servido_at)) * 24 * 60) AS minutos
  FROM platos_servidos ps
  JOIN platos p ON p.id = ps.plato_id
  JOIN (SELECT plato_servido_id, MIN(desde) AS desde
          FROM estados_plato WHERE estado = 'vacio'
         GROUP BY plato_servido_id) v ON v.plato_servido_id = ps.id
 GROUP BY p.id
 ORDER BY minutos DESC
"""
# Sobra por plato: cuánto le quedaba (fill_ratio) en la última lectura, de los
# platos que dejaron de verse hace más de GONE_AFTER_MIN mientras la cámara
# seguía leyendo (se los llevaron).
SQL_LEFTOVER = """
SELECT p.nombre AS plato, COUNT(*) AS platos, AVG(ps.ultimo_fill_ratio) AS sobra
  FROM platos_servidos ps
  JOIN platos p ON p.id = ps.plato_id
 WHERE ps.ultimo_fill_ratio IS NOT NULL
   AND (julianday((SELECT MAX(visto_at) FROM platos_servidos))
        - julianday(ps.visto_at)) * 24 * 60 > ?
 GROUP BY p.id
 ORDER BY sobra DESC
"""
# Adopción: de los avisos con botones (hoy solo plato lleno; con C-5, todos),
# cuántos tuvieron respuesta y cuántos se marcaron "No hacía falta".
SQL_ADOPTION = """
SELECT COUNT(*) AS avisos,
       SUM(EXISTS (SELECT 1 FROM respuestas r WHERE r.aviso_id = a.id))
         AS respondidos,
       SUM(EXISTS (SELECT 1 FROM respuestas r WHERE r.aviso_id = a.id
                     AND r.texto LIKE 'No hacía falta%')) AS no_hacia_falta
  FROM avisos a
 WHERE a.tipo = 'plato_lleno_mucho' AND a.resuelto_at IS NOT NULL
"""
# Espera del siguiente plato: mesa terminada que se cerró porque llegó otro
# plato (no por retiro): minutos que la mesa estuvo sin nada para comer.
SQL_NEXT_DISH = """
SELECT COALESCE(m.nombre, '(sin mozo)') AS mozo, COUNT(*) AS veces,
       AVG((julianday(a.resuelto_at) - julianday(a.abierto_at)) * 24 * 60) AS minutos
  FROM avisos a LEFT JOIN mozos m ON m.id = a.mozo_id
 WHERE a.tipo = 'mesa_terminada' AND a.motivo_cierre = 'servido'
 GROUP BY a.mozo_id
 ORDER BY minutos DESC
"""
# Para las horas pico: cada aviso con su hora y, si es mesa terminada, cuánto esperó.
SQL_ALERT_TIMES = """
SELECT abierto_at,
       CASE WHEN tipo = 'mesa_terminada' AND motivo_cierre = 'retirados'
            THEN (julianday(resuelto_at) - julianday(abierto_at)) * 24 * 60 END
         AS espera
  FROM avisos
"""
# Para la carga por mozo: quién atiende cada mesa.
SQL_TABLE_WAITER = """
SELECT me.id AS mesa, COALESCE(m.nombre, '(sin mozo)') AS mozo
  FROM mesas me LEFT JOIN mozos m ON m.id = me.mozo_id
"""
# Para armar las visitas de cada mesa (rotación): platos servidos y cierres.
SQL_TABLE_EVENTS = """
SELECT mesa_id, servido_at AS at, NULL AS fin FROM platos_servidos
UNION ALL
SELECT mesa_id, abierto_at AS at, resuelto_at AS fin FROM avisos
 WHERE tipo = 'mesa_terminada' AND motivo_cierre = 'retirados'
 ORDER BY mesa_id, at
"""


def _time(ts: str) -> datetime:
    """Hora de la base sin zona (el historial no tiene; lo de la cámara es UTC)."""
    return datetime.fromisoformat(ts.removesuffix("Z")).replace(tzinfo=None)


def _service(at: datetime) -> tuple[str, str]:
    kind = "almuerzo" if at.hour < LUNCH_UNTIL_HOUR else "cena"
    return at.date().isoformat(), kind


def table_visits(db: Database) -> list[tuple[str, datetime, datetime]]:
    """Visitas de mesa: (mesa, primer plato servido, platos retirados). Una visita
    empieza con el primer plato después del último retiro y termina con el
    retiro de la mesa terminada. Lo que queda sin retirar no cuenta."""
    visits = []
    start: dict[str, datetime] = {}
    for r in db.query(SQL_TABLE_EVENTS):
        at = _time(r["at"])
        if r["fin"] is None:
            start.setdefault(r["mesa_id"], at)
        elif r["mesa_id"] in start:
            visits.append((r["mesa_id"], start.pop(r["mesa_id"]), _time(r["fin"])))
    return visits


def rotation(db: Database) -> dict | None:
    """Tiempo de mesa y rotación: minutos por visita y mesas por servicio
    (almuerzo o cena de un día), en total y por tipo de servicio."""
    visits = table_visits(db)
    if not visits:
        return None
    by_kind: dict[str, list[float]] = {}
    services: dict[str, set] = {}
    for _, start, end in visits:
        day, kind = _service(start)
        by_kind.setdefault(kind, []).append((end - start).total_seconds() / 60)
        services.setdefault(kind, set()).add(day)
    minutes = [m for ms in by_kind.values() for m in ms]
    return {
        "visits": len(visits),
        "minutes": sum(minutes) / len(minutes),
        "services": sum(len(s) for s in services.values()),
        "per_service": len(visits) / sum(len(s) for s in services.values()),
        "by_kind": {
            kind: (sum(ms) / len(ms), len(ms) / len(services[kind]))
            for kind, ms in sorted(by_kind.items())
        },
    }


def waiter_load(db: Database, visits: list) -> list[tuple[str, float, float]]:
    """Carga por mozo: mesas atendidas por servicio y minutos promedio hasta
    retirar una mesa terminada (de `SQL_ATTEND`). De la más cargada a la menos."""
    waiter = {r["mesa"]: r["mozo"] for r in db.query(SQL_TABLE_WAITER)}
    attend = {r["mozo"]: r["minutos"] for r in db.query(SQL_ATTEND)}
    tables: dict[str, int] = {}
    services: dict[str, set] = {}
    for table_id, start, _ in visits:
        name = waiter.get(table_id, "(sin mozo)")
        tables[name] = tables.get(name, 0) + 1
        services.setdefault(name, set()).add(_service(start))
    rows = [(n, c / len(services[n]), attend.get(n)) for n, c in tables.items()]
    return sorted(rows, key=lambda r: -r[1])


def peak_hours(db: Database) -> list[tuple[str, int, float | None]]:
    """Horas pico: por franja de PEAK_SLOT_MIN, avisos y % de mesas terminadas que
    esperaron más de UNATTENDED_LIMIT_MIN. Ordenado por hora."""
    slots: dict[str, list] = {}
    for r in db.query(SQL_ALERT_TIMES):
        at = _time(r["abierto_at"])
        minute = at.minute - at.minute % PEAK_SLOT_MIN
        slot = slots.setdefault(f"{at.hour:02d}:{minute:02d}", [0, 0, 0])
        slot[0] += 1
        if r["espera"] is not None:
            slot[1] += 1
            slot[2] += r["espera"] > UNATTENDED_LIMIT_MIN
    return [
        (slot, n, late / ended if ended else None)
        for slot, (n, ended, late) in sorted(slots.items())
    ]


def operation(db: Database) -> dict:
    """Métricas de operación de M-1 que no estaban en el reporte del pitch."""
    unattended = db.query(SQL_UNATTENDED, (UNATTENDED_LIMIT_MIN,))[0]
    waste = [(r["plato"], r["platos"], r["pesos"]) for r in db.query(SQL_WASTE)]
    rot = rotation(db)
    visits = table_visits(db) if rot else []
    adoption = db.query(SQL_ADOPTION)[0]
    return {
        "unattended": dict(unattended) if unattended["mesas"] else None,
        "forgotten": [
            (r["mozo"], r["avisos"], r["olvidadas"])
            for r in db.query(SQL_FORGOTTEN, (FORGOTTEN_LIMIT_S,))
        ],
        "rotation": rot,
        "waste": waste,
        "waste_total": sum(p for _, _, p in waste),
        "services": rot["services"] if rot else 0,
        "load": waiter_load(db, visits),
        "peaks": peak_hours(db),
        "pace": [(r["plato"], r["platos"], r["minutos"]) for r in db.query(SQL_PACE)],
        "leftover": [
            (r["plato"], r["platos"], r["sobra"])
            for r in db.query(SQL_LEFTOVER, (GONE_AFTER_MIN,))
        ],
        "adoption": dict(adoption) if adoption["avisos"] else None,
        "next_dish": [
            (r["mozo"], r["veces"], r["minutos"]) for r in db.query(SQL_NEXT_DISH)
        ],
    }


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
        "ops": operation(db),
    }


# Métricas de roles.py que muestra este reporte (los roles filtran por estas).
REPORT_METRICS = (
    "tiempo_respuesta",
    "mesa_terminada_sin_atender",
    "mesas_olvidadas",
    "rotacion",
    "platos_que_vuelven",
    "desperdicio",
    "carga_por_mozo",
    "horas_pico",
    "ritmo_por_plato",
    "sobra_por_plato",
    "adopcion",
    "espera_siguiente_plato",
)
# Las que van en la imagen del pitch (render).
IMAGE_METRICS = ("platos_que_vuelven", "tiempo_respuesta")


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
    print_operation(m["ops"], show)
    if m["live_answers"]:
        print(f"\n(incluye {m['live_answers']} respuestas reales de las demos)")


def print_operation(ops: dict, show: set[str]) -> None:
    if ops["unattended"] and "mesa_terminada_sin_atender" in show:
        u = ops["unattended"]
        print(
            f"\nMesa terminada sin atender ({u['mesas']} mesas)\n"
            f"  promedio {u['promedio']:.1f} min · peor {u['peor']:.0f} min · "
            f"{u['fuera_de_tiempo'] * 100:.0f} % esperó más de "
            f"{UNATTENDED_LIMIT_MIN} min"
        )
    if ops["forgotten"] and "mesas_olvidadas" in show:
        print(
            f"\nMesas olvidadas (aviso de plato lleno sin respuesta en "
            f"{FORGOTTEN_LIMIT_S // 60} min)"
        )
        for waiter, alerts, forgotten in ops["forgotten"]:
            print(
                f"  {waiter:10} {forgotten:3} de {alerts:3} avisos"
                f"  ({forgotten / alerts * 100:4.0f} %)"
            )
    if ops["rotation"] and "rotacion" in show:
        r = ops["rotation"]
        print(
            f"\nTiempo de mesa y rotación ({r['visits']} visitas, "
            f"{r['services']} servicios)\n"
            f"  {r['minutes']:.0f} min por mesa · {r['per_service']:.1f} mesas por servicio"
        )
        for kind, (minutes, per_service) in r["by_kind"].items():
            print(f"  {kind:10} {minutes:4.0f} min · {per_service:.1f} mesas")
    if ops["waste"] and "desperdicio" in show:
        per = ops["waste_total"] / ops["services"] if ops["services"] else 0
        print(
            f"\nDesperdicio: ${ops['waste_total']:,.0f} en platos que volvieron"
            f" por un problema (${per:,.0f} por servicio)"
        )
        for dish, plates, pesos in ops["waste"]:
            print(f"  {dish:34} {plates:3} platos  ${pesos:>8,.0f}")
    if ops["load"] and "carga_por_mozo" in show:
        print("\nCarga por mozo (mesas por servicio · minutos hasta retirar)")
        for waiter, per_service, attend in ops["load"]:
            took = f"{attend:4.1f} min" if attend is not None else "   -"
            print(f"  {waiter:10} {per_service:4.1f} mesas · {took}")
    if ops["peaks"] and "horas_pico" in show:
        print(
            f"\nHoras pico (avisos por franja de {PEAK_SLOT_MIN} min · % de mesas"
            f" terminadas que esperaron más de {UNATTENDED_LIMIT_MIN} min)"
        )
        for slot, alerts, late in ops["peaks"]:
            pct = f"{late * 100:3.0f} %" if late is not None else "   -"
            print(f"  {slot}  {alerts:4} avisos · {pct}")
    if ops["pace"] and "ritmo_por_plato" in show:
        print("\nRitmo por plato (minutos de servido a vacío)")
        for dish, plates, minutes in ops["pace"]:
            print(f"  {dish:34} {minutes:5.1f} min  ({plates} platos)")
    if ops["leftover"] and "sobra_por_plato" in show:
        print("\nSobra por plato (cuánto le quedaba al retirarlo)")
        for dish, plates, left in ops["leftover"]:
            print(f"  {dish:34} {left * 100:4.0f} %  ({plates} platos)")
    if ops["adoption"] and "adopcion" in show:
        a = ops["adoption"]
        print(
            f"\nAdopción: {a['respondidos']} de {a['avisos']} avisos respondidos"
            f" ({a['respondidos'] / a['avisos'] * 100:.0f} %),"
            f" {a['no_hacia_falta']} marcados «No hacía falta»"
        )
    if ops["next_dish"] and "espera_siguiente_plato" in show:
        print("\nEspera del siguiente plato (mesa sin nada hasta que llega otro)")
        for waiter, times, minutes in ops["next_dish"]:
            print(f"  {waiter:10} {minutes:4.1f} min  ({times} veces)")


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
