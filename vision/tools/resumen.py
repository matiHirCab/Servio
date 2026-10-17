"""Tabla de lo que pasó en una corrida, para el cierre de la demo (JES-10).

Lee `out/eventos.jsonl` (03-contratos.md, «Log») y muestra, por mesa:
- cuántas comidas hubo y cuánto duraron en promedio (desde que aparece el
  primer plato hasta que la mesa queda sin platos);
- cuántos avisos de cada tipo se abrieron y cuántos recordatorios salieron;
- cuánto tardaron en promedio los avisos en resolverse.
Al final, los totales.

El log no se borra entre corridas (se agrega al final). Las corridas se
cortan con la misma regla que `tools/replay.py` (de Matías): en cada
`run_started` y, en logs sin esa línea, cuando el `ts` vuelve atrás o salta
más de `RUN_GAP_READINGS` lecturas. Los avisos van con la corrida en la que
aparecen en el archivo. Por defecto se resume la última corrida; con `--all`,
todas (cada una por separado y sumadas).

Acepta también `fixtures/observations/*.jsonl` (líneas sin `event`, que son
observaciones). Una línea inválida se avisa y se saltea.

    python tools/resumen.py [--file out/eventos.jsonl] [--image out/resumen.png] [--all]
"""

import argparse
import json
import sys
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from replay import RUN_GAP_READINGS, Run, _continues

from servio_vision.alerts.config import DEFAULT_INTERVAL
from servio_vision.alerts.models import TableObservation

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
    meals_in_progress: int = 0
    alerts: Counter = field(default_factory=Counter)  # tipo → avisos abiertos
    reminders: int = 0
    resolve_times: list[float] = field(default_factory=list)  # segundos

    def add(self, other: "TableSummary") -> None:
        self.meals += other.meals
        self.meals_in_progress += other.meals_in_progress
        self.alerts.update(other.alerts)
        self.reminders += other.reminders
        self.resolve_times += other.resolve_times


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value)


def load_runs(path: Path, interval: float = DEFAULT_INTERVAL) -> list[list[dict]]:
    """Corta el log en corridas (regla de `replay.read_runs`); cada una con sus eventos
    en el orden del archivo, sin la línea `run_started`."""
    max_gap = RUN_GAP_READINGS * interval
    runs: list[tuple[Run, list[dict]]] = []
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    for number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            data = json.loads(line)
            if not isinstance(data, dict):
                raise TypeError("no es un objeto JSON")
            kind = data.get("event", "observation")
            if kind == "run_started":
                runs.append((Run(marked=True), []))
                continue
            if kind == "observation":
                fields = {k: v for k, v in data.items() if k != "event"}
                obs = TableObservation.model_validate(fields)
                if not runs or not _continues(runs[-1][0], obs, max_gap):
                    runs.append((Run(), []))
                runs[-1][0].observations.append(obs)
                event = {**data, "event": "observation"}
            else:
                _ts(data["ts"])
                if not runs:
                    runs.append((Run(), []))
                event = data
        except (ValueError, TypeError, KeyError) as error:
            print(f"Aviso: {path}:{number}: línea inválida, la salteo ({error}).")
            continue
        runs[-1][1].append(event)
    return [events for _, events in runs if events]


def summarize(events: list[dict]) -> dict[str, TableSummary]:
    """Resumen de una corrida, recorriendo los eventos en el orden del archivo.

    Una observación con `ts` anterior a la última de su mesa se ignora: `alerts` la
    escribe en el log aunque la rechace, y cortaría una comida por la mitad.
    """
    tables: dict[str, TableSummary] = defaultdict(TableSummary)
    meal_start: dict[str, datetime] = {}
    last_obs: dict[str, datetime] = {}
    opened_at: dict[str, datetime] = {}  # id del aviso → cuándo se abrió

    for event in events:
        table_id, kind, ts = event["table_id"], event["event"], _ts(event["ts"])
        summary = tables[table_id]
        if kind == "observation":
            if table_id in last_obs and ts < last_obs[table_id]:
                continue
            last_obs[table_id] = ts
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
        tables[table_id].meals_in_progress = 1
    return dict(sorted(tables.items()))


def summarize_runs(runs: list[list[dict]]) -> dict[str, TableSummary]:
    """Cada corrida por separado (no se mezclan comidas ni avisos) y después sumadas."""
    tables: dict[str, TableSummary] = defaultdict(TableSummary)
    for events in runs:
        for table_id, summary in summarize(events).items():
            tables[table_id].add(summary)
    return dict(sorted(tables.items()))


def _minutes(seconds: list[float]) -> str:
    if not seconds:
        return "-"
    return f"{sum(seconds) / len(seconds) / 60:.1f} min"


def _row(name: str, s: TableSummary, other: bool) -> list[str]:
    meals = str(len(s.meals))
    if s.meals_in_progress:
        meals += f" (+{s.meals_in_progress} en curso)"
    others = sum(n for t, n in s.alerts.items() if t not in ALERT_TYPES)
    return [
        name,
        meals,
        _minutes(s.meals),
        *(str(s.alerts[t]) for t in ALERT_TYPES),
        *([str(others)] if other else []),
        str(s.reminders),
        _minutes(s.resolve_times),
    ]


def rows(tables: dict[str, TableSummary]) -> list[list[str]]:
    """Filas de la tabla (con encabezado y una fila de totales al final).

    Si hubo avisos de un tipo que la tabla no conoce, se agrega una columna "Otros".
    """
    total = TableSummary()
    for s in tables.values():
        total.add(s)
    other = any(t not in ALERT_TYPES for t in total.alerts)
    headers = list(HEADERS)
    if other:
        headers.insert(HEADERS.index("Recordatorios"), "Otros")
    body = [_row(table_id, s, other) for table_id, s in tables.items()]
    return [headers, *body, _row("Total", total, other)]


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
    parser.add_argument("--all", action="store_true", help="todas las corridas del log")
    args = parser.parse_args(argv)

    for stream in (sys.stdout, sys.stderr):  # consola de Windows (cp1252)
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    if not args.file.exists():
        print(f"No existe {args.file}: corré primero main.py o la vista de demo.")
        return 2
    runs = load_runs(args.file)
    if not runs:
        print(f"{args.file} no tiene eventos.")
        return 1
    table = rows(summarize_runs(runs if args.all else runs[-1:]))
    print(format_table(table))
    if args.image:
        render_image(table, args.image)
        print(f"\nImagen guardada en {args.image}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
