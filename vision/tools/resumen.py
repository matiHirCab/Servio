"""Tabla de lo que pasó en una corrida, para el cierre de la demo (JES-10).

Lee `out/eventos.jsonl` (03-contratos.md, «Log») y muestra, por mesa:
- cuántas comidas hubo y cuánto duraron en promedio (desde que aparece el
  primer plato hasta que la mesa queda sin platos);
- cuántos avisos de cada tipo se abrieron y cuántos recordatorios salieron;
- cuánto tardaron en promedio los avisos en resolverse.
Al final, los totales.

    python tools/resumen.py [--file out/eventos.jsonl] [--image out/resumen.png]
"""

import argparse
import json
import sys
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

VISION_DIR = Path(__file__).resolve().parents[1]
DEFAULT_LOG = VISION_DIR / "out" / "eventos.jsonl"

ALERT_TYPES = {
    "mesa_por_terminar": "Por terminar",
    "plato_vacio": "Platos vacíos",
    "plato_lleno_mucho": "Plato lleno",
}
HEADERS = [
    "Mesa",
    "Comidas",
    "Duración media",
    *ALERT_TYPES.values(),
    "Recordatorios",
    "Resolución media",
]


@dataclass
class TableSummary:
    meals: list[float] = field(default_factory=list)  # segundos de cada comida
    meal_in_progress: bool = False
    alerts: Counter = field(default_factory=Counter)  # tipo → avisos abiertos
    reminders: int = 0
    resolve_times: list[float] = field(default_factory=list)  # segundos


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value)


def load_events(path: Path) -> list[dict]:
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def summarize(events: list[dict]) -> dict[str, TableSummary]:
    tables: dict[str, TableSummary] = defaultdict(TableSummary)
    meal_start: dict[str, datetime] = {}
    opened_at: dict[str, datetime] = {}  # id del aviso → cuándo se abrió

    for event in sorted(events, key=lambda e: _ts(e["ts"])):
        table_id, kind, ts = event["table_id"], event["event"], _ts(event["ts"])
        summary = tables[table_id]
        if kind == "observation":
            has_plates = bool(event["items"])
            if has_plates and table_id not in meal_start:
                meal_start[table_id] = ts
            elif not has_plates and table_id in meal_start:
                start = meal_start.pop(table_id)
                summary.meals.append((ts - start).total_seconds())
        elif kind == "alert_opened":
            summary.alerts[event["type"]] += 1
            opened_at[event["id"]] = ts
        elif kind == "alert_reminder":
            summary.reminders += 1
        elif kind == "alert_resolved" and event["id"] in opened_at:
            summary.resolve_times.append(
                (ts - opened_at.pop(event["id"])).total_seconds()
            )

    for table_id in meal_start:
        tables[table_id].meal_in_progress = True
    return dict(sorted(tables.items()))


def _minutes(seconds: list[float]) -> str:
    if not seconds:
        return "-"
    return f"{sum(seconds) / len(seconds) / 60:.1f} min"


def _row(name: str, s: TableSummary) -> list[str]:
    meals = str(len(s.meals)) + (" (+1 en curso)" if s.meal_in_progress else "")
    return [
        name,
        meals,
        _minutes(s.meals),
        *(str(s.alerts[t]) for t in ALERT_TYPES),
        str(s.reminders),
        _minutes(s.resolve_times),
    ]


def rows(tables: dict[str, TableSummary]) -> list[list[str]]:
    """Filas de la tabla (con encabezado y una fila de totales al final)."""
    total = TableSummary()
    for s in tables.values():
        total.meals += s.meals
        total.meal_in_progress |= s.meal_in_progress
        total.alerts.update(s.alerts)
        total.reminders += s.reminders
        total.resolve_times += s.resolve_times
    body = [_row(table_id, s) for table_id, s in tables.items()]
    if total.meal_in_progress:
        in_progress = sum(s.meal_in_progress for s in tables.values())
        total_row = _row("Total", total)
        total_row[1] = f"{len(total.meals)} (+{in_progress} en curso)"
    else:
        total_row = _row("Total", total)
    return [HEADERS, *body, total_row]


def format_table(table: list[list[str]]) -> str:
    widths = [max(len(row[i]) for row in table) for i in range(len(table[0]))]

    def line(row: list[str]) -> str:
        return " | ".join(cell.ljust(width) for cell, width in zip(row, widths))

    rule = "-+-".join("-" * w for w in widths)
    return "\n".join(
        [line(table[0]), rule, *map(line, table[1:-1]), rule, line(table[-1])]
    )


FONT_CANDIDATES = [
    ("arial.ttf", "arialbd.ttf"),  # Windows
    ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf"),  # Linux
    ("Arial.ttf", "Arial Bold.ttf"),  # macOS
]


def _fonts():
    """(normal, negrita, hace_falta_sacar_acentos). La fuente de Pillow no tiene acentos."""
    from PIL import ImageFont

    for regular, bold in FONT_CANDIDATES:
        try:
            return ImageFont.truetype(regular, 28), ImageFont.truetype(bold, 30), False
        except OSError:
            continue
    return ImageFont.load_default(size=28), ImageFont.load_default(size=30), True


def _strip_accents(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text)
    return "".join(c for c in normalized if not unicodedata.combining(c))


def render_image(table: list[list[str]], path: Path) -> None:
    """La misma tabla como imagen, para la última slide."""
    from PIL import Image, ImageDraw

    font, bold, ascii_only = _fonts()
    title = "Servio: lo que pasó en la demo"
    if ascii_only:
        table = [[_strip_accents(cell) for cell in row] for row in table]
        title = _strip_accents(title)
    pad, row_h = 24, 52
    measure = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    widths = [
        max(measure.textlength(row[i], font=bold) for row in table) + 2 * pad
        for i in range(len(table[0]))
    ]
    title_h = 80
    image = Image.new(
        "RGB", (int(sum(widths)), title_h + row_h * len(table) + pad), (24, 24, 28)
    )
    draw = ImageDraw.Draw(image)
    draw.text((pad, 22), title, font=bold, fill=(255, 255, 255))
    for r, row in enumerate(table):
        y = title_h + r * row_h
        header_or_total = r == 0 or r == len(table) - 1
        if r % 2 == 1:
            draw.rectangle([0, y, image.width, y + row_h], fill=(34, 34, 40))
        if header_or_total:
            draw.rectangle([0, y, image.width, y + row_h], fill=(46, 64, 90))
        x = 0
        for c, cell in enumerate(row):
            draw.text(
                (x + pad, y + 10),
                cell,
                font=bold if header_or_total else font,
                fill=(255, 255, 255),
            )
            x += widths[c]
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Tabla de lo que pasó en una corrida.")
    parser.add_argument("--file", type=Path, default=DEFAULT_LOG)
    parser.add_argument(
        "--image", type=Path, help="guardar también la tabla como imagen"
    )
    args = parser.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if not args.file.exists():
        print(f"No existe {args.file}: corré primero main.py o la vista de demo.")
        return 2
    table = rows(summarize(load_events(args.file)))
    print(format_table(table))
    if args.image:
        render_image(table, args.image)
        print(f"\nImagen guardada en {args.image}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
