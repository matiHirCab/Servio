"""Evaluación (TOM-6, B-3): acierto de Claude sobre recortes etiquetados y el tablero
de números.

**Acierto por estado.** El set es una carpeta con `labels.csv` (`archivo,estado[,tipo,nota]`,
el que escribe `tools/etiquetar.py`) y las imágenes de cada plato. Los recortes se
mandan a Claude de a `--por-llamada` (como los platos de una mesa), con una hoja de
todos ellos como imagen de la mesa.

  python tools/eval.py --set data/eval
  python tools/eval.py --set data/eval --por-llamada 6 --max 30

**Tablero de números** (`--all`, decisión 16: el modelo sigue siendo Haiku; el tablero
sirve para saber si un prompt empeora, no para cambiar de modelo): acierto por estado,
escenarios de `fixtures/observations/` que pasan por el pipeline entero, avisos falsos
por hora (cuando C-5 traiga «No hacía falta»), latencia del modelo y costo por mesa por
hora. Se guarda con fecha en `out/eval/tablero-<fecha>.json` y se compara con el
anterior, para ver qué cambió entre dos versiones.

  python tools/eval.py --all                      # con data/eval si existe
  python tools/eval.py --all --set data/eval/cena
  python tools/eval.py --all --sin-modelo         # solo los escenarios (sin llamadas)
  python tools/eval.py --comparar out/eval/tablero-A.json out/eval/tablero-B.json

Las imágenes no van al repo (ver 09-convenciones.md). El set del equipo está en la
carpeta compartida del proyecto, `servio-eval/`.
"""

import argparse
import contextlib
import io
import json
import logging
import os
import sys
import tempfile
import time
from collections import Counter
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, get_args

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import etiquetar  # vecino en tools/: las etiquetas se leen igual que se escriben
from PIL import Image

from servio_vision import alerts, settings, utf8_console
from servio_vision.llm import EstimateRequest, make_estimator
from servio_vision.schemas import PlateState, state_from_fill_ratio

STATES: list[str] = list(get_args(PlateState))  # los del contrato, en su orden
CROP_SIDE = 384
DEFAULT_SET = Path("data/eval")
DEFAULT_OUT = Path("out/eval")
BOARD_PREFIX = "tablero-"
FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
SCENARIO_INTERVAL = 30  # la cadencia con la que están escritos los escenarios
FRAME_SIZE = (200, 100)  # las fotos sintéticas que el fake no mira


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


def load_labels(folder: Path) -> list[dict[str, str]]:
    """Las filas de `labels.csv` con un estado de verdad (`etiquetar.read_labels`:
    un archivo repetido vale por su última fila); las saltadas no cuentan."""
    rows = etiquetar.read_labels(folder).values()
    return [r for r in rows if r.get("estado") in STATES]


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


def accuracy_by_state(summary: dict) -> dict[str, float | None]:
    """Acierto de cada estado real (la diagonal de la matriz sobre su fila)."""
    out: dict[str, float | None] = {}
    for real in STATES:
        row = summary["matriz"].get(real)
        if not row:
            out[real] = None
            continue
        total = sum(row.values())
        out[real] = round(row.get(real, 0) / total, 3) if total else None
    return out


def evaluate_set(
    folder: Path, estimator, per_call: int = 8, limit: int = 0, log=print
) -> tuple[list[dict], list[float]]:
    """Manda los recortes del set al estimador de a `per_call` y devuelve, por plato,
    lo predicho; más la latencia de cada llamada."""
    labels = load_labels(folder)
    if limit:
        labels = labels[:limit]
    results, latencies = [], []
    for start in range(0, len(labels), per_call):
        batch = labels[start : start + per_call]
        imgs = [Image.open(folder / r["archivo"]) for r in batch]
        t0 = time.monotonic()
        report = estimator.estimate(
            EstimateRequest(
                [jpeg(i, CROP_SIDE) for i in imgs],
                f"EVAL{start // per_call + 1}",
            )
        )
        latencies.append(time.monotonic() - t0)
        # por `index`, no por posición: una respuesta que saltea un plato no corre
        # las demás, y un plato sin respuesta cuenta como tal
        by_index = {p.index: p for p in report.plates} if report else {}
        for n, r in enumerate(batch, 1):
            est = by_index.get(n)
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
        log(f"{len(results)}/{len(labels)} platos ({latencies[-1]:.1f} s)")
    return results, latencies


# ---------------------------------------------------------------- escenarios


def scenario_names() -> list[str]:
    return sorted(p.stem for p in (FIXTURES / "observations").glob("*.jsonl"))


def alert_events(log: Path) -> list[dict]:
    """Los avisos del log con el formato de `fixtures/pipeline/`: segundos desde la
    primera observación. `ValueError` si el log no tiene observaciones."""
    events = [json.loads(line) for line in log.read_text("utf-8").splitlines()]
    first = next((e["ts"] for e in events if e.get("event") == "observation"), None)
    if first is None:
        raise ValueError(f"{log.name} no tiene observaciones")
    start = datetime.fromisoformat(first)
    return [
        {
            "at_seconds": int(
                (datetime.fromisoformat(e["ts"]) - start).total_seconds()
            ),
            "event": e["event"],
            "type": e["type"],
            "table_id": e["table_id"],
        }
        for e in events
        if e["event"].startswith("alert_")
    ]


@contextlib.contextmanager
def _isolated_run(workdir: Path) -> Iterator[None]:
    """`main.py` escribe `out/eventos.jsonl` y la base donde se lo corre y lee el
    entorno: se corre en una carpeta temporal, con umbrales de producción y una
    SQLite propia, sin nada del `.env` de esta máquina (ni lo que `load_dotenv` ya
    haya metido en el entorno), y se vuelve todo atrás al salir. Lo que el pipeline
    imprime (los avisos por consola, el chequeo de encuadre de una foto negra) no es
    del tablero: se calla, salvo los errores. Cada cosa que se cambia se deshace en
    el mismo `ExitStack`, así una que falle a mitad de camino no deja nada a medias."""
    servio_log = logging.getLogger("servio")
    with contextlib.ExitStack() as stack:
        stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        stack.callback(servio_log.setLevel, servio_log.level)
        servio_log.setLevel(logging.ERROR)
        saved_env = dict(os.environ)

        def restore_env() -> None:
            os.environ.clear()
            os.environ.update(saved_env)

        stack.callback(restore_env)
        # ninguna variable de configuración de la casa ni de los avisos
        for name in settings.Settings.model_fields:
            os.environ.pop(
                settings._alias(name).upper(), None
            )  # SERVIO_DEMO entre ellas
        os.environ.pop("DEMO_MODE", None)
        os.environ["DEMO_MODE"] = "false"
        os.environ["DATABASE_URL"] = f"sqlite:///{(workdir / 'eval.db').as_posix()}"
        saved_log, saved_env_file = alerts.DEFAULT_LOG, settings.ENV_FILE

        def restore_modules() -> None:
            alerts.DEFAULT_LOG, settings.ENV_FILE = saved_log, saved_env_file

        stack.callback(restore_modules)
        alerts.DEFAULT_LOG = workdir / "eventos.jsonl"
        settings.ENV_FILE = None

        def reset_run_state() -> None:
            # lo que la corrida deja armado (el procesador de avisos con su base, la
            # configuración activa) no tiene que llegar al escenario siguiente
            if alerts._processor is not None:
                alerts._processor.close()
                alerts._processor = None
            settings._active = None

        stack.callback(reset_run_state)
        stack.callback(os.chdir, Path.cwd())
        (workdir / "frames").mkdir()
        os.chdir(workdir)
        yield


def run_scenario(name: str, workdir: Path) -> dict[str, Any]:
    """Pasa un escenario por el pipeline entero (`main.py --classifier fake
    --fake-mode replay`, como `tests/test_e2e_pipeline.py`) y compara sus avisos con
    `fixtures/pipeline/<escenario>.alerts.json`."""
    import cv2
    import numpy as np

    import main as loop
    from servio_vision.zones import TableZone, Zones, save_zones

    fixture = FIXTURES / "observations" / f"{name}.jsonl"
    golden = FIXTURES / "pipeline" / f"{name}.alerts.json"
    lines = [json.loads(line) for line in fixture.read_text("utf-8").splitlines()]
    table_ids = sorted({line["table_id"] for line in lines})
    readings = len(lines) // len(table_ids)
    with _isolated_run(workdir):
        frames = workdir / "frames"
        frame = np.zeros((FRAME_SIZE[1], FRAME_SIZE[0], 3), np.uint8)
        for n in range(readings):
            cv2.imwrite(str(frames / f"{n:04d}.png"), frame)
        width = FRAME_SIZE[0] // len(table_ids)
        zones = Zones(
            frame_size=FRAME_SIZE,
            tables=[
                TableZone(
                    table_id=t,
                    name=t,
                    polygon=[
                        (i * width, 0),
                        ((i + 1) * width - 1, 0),
                        ((i + 1) * width - 1, FRAME_SIZE[1] - 1),
                        (i * width, FRAME_SIZE[1] - 1),
                    ],
                )
                for i, t in enumerate(table_ids)
            ],
        )
        zones_path = workdir / "zones.json"
        save_zones(zones, zones_path)
        failure = None
        try:
            code = loop.main(
                [
                    "--source", str(frames),
                    "--zones", str(zones_path),
                    "--classifier", "fake",
                    "--fake-mode", "replay",
                    "--fixture", str(fixture),
                    "--interval", str(SCENARIO_INTERVAL),
                    "--no-telegram",
                ]
            )  # fmt: skip
        except Exception as e:  # noqa: BLE001  (se informa en el tablero, no se oculta)
            code = -1
            failure = f"el pipeline reventó: {type(e).__name__}: {e}"
        got: list[dict] = []
        if failure is None and code != 0:
            failure = f"el pipeline salió con {code}"
        if failure is None:
            try:
                got = alert_events(alerts.DEFAULT_LOG)
            except (OSError, ValueError) as e:  # sin log, o sin observaciones
                failure = f"la corrida no dejó avisos que comparar: {e}"
    expected = json.loads(golden.read_text("utf-8")) if golden.exists() else None
    passes = failure is None and expected is not None and got == expected
    detail = []
    if failure is not None:
        detail.append(failure)
    elif expected is None:
        detail.append("sin esperado en fixtures/pipeline/")
    elif not passes:
        want, have = (
            Counter(json.dumps(e) for e in expected),
            Counter(json.dumps(e) for e in got),
        )
        detail += [f"esperado y no salió: {e}" for e in (want - have).elements()]
        detail += [f"salió y no se esperaba: {e}" for e in (have - want).elements()]
        if not detail:
            detail.append("los mismos avisos, en otro orden")
    return {
        "escenario": name,
        "pasa": passes,
        "corrida_ok": failure
        is None,  # la corrida anduvo: sus avisos valen para regenerar
        "diferencias": detail,
        "avisos": got,
    }


def run_scenarios(names: list[str] | None = None) -> list[dict[str, Any]]:
    results = []
    for name in names or scenario_names():
        with tempfile.TemporaryDirectory(prefix="servio-eval-") as tmp:
            results.append(run_scenario(name, Path(tmp)))
    return results


# ---------------------------------------------------------------- tablero


def cost_summary(meter, calls_per_hour_per_table: float) -> dict[str, Any]:
    """Lo que costó el modelo en esta corrida y qué da por mesa por hora. El costo
    en dólares lo informa solo el CLI de Claude; con la API quedan los tokens."""
    calls = meter.calls
    per_call = meter.cost_usd / calls if calls and meter.cost_usd else None
    return {
        "llamadas": calls,
        "tokens_entrada_por_llamada": round(meter.input_tokens / calls)
        if calls
        else None,
        "tokens_salida_por_llamada": round(meter.output_tokens / calls)
        if calls
        else None,
        "costo_por_llamada_usd": round(per_call, 5) if per_call is not None else None,
        "costo_mesa_hora_usd": round(per_call * calls_per_hour_per_table, 4)
        if per_call is not None
        else None,
        "nota": None
        if per_call is not None
        else "el proveedor no informó costo (solo el CLI de Claude lo hace): quedan los tokens",
    }


def build_board(
    *,
    accuracy: dict | None,
    scenarios: list[dict[str, Any]],
    latencies: list[float],
    cost: dict[str, Any] | None,
    set_dir: Path | None,
    interval: float,
) -> dict[str, Any]:
    """El tablero de números de una corrida, listo para guardar y comparar."""
    passed = sum(s["pasa"] for s in scenarios)
    return {
        "fecha": datetime.now(UTC).isoformat(timespec="seconds"),
        "set": str(set_dir) if set_dir else None,
        "acierto": accuracy["acierto"] if accuracy else None,
        "acierto_por_estado": accuracy_by_state(accuracy) if accuracy else None,
        "platos": accuracy["platos"] if accuracy else 0,
        "escenarios": {
            "pasan": passed,
            "total": len(scenarios),
            "detalle": [
                {k: v for k, v in s.items() if k not in ("avisos", "corrida_ok")}
                for s in scenarios
            ],
        },
        "avisos_falsos_por_hora": None,
        "avisos_falsos_nota": "sale de «No hacía falta» (C-5) y de los escenarios de B-2; todavía no hay datos",
        "latencia_media_s": round(sum(latencies) / len(latencies), 1)
        if latencies
        else None,
        "intervalo_s": interval,
        "costo": cost,
    }


def save_board(board: dict[str, Any], out: Path) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.fromisoformat(board["fecha"]).strftime("%Y%m%d-%H%M%S")
    path = out / f"{BOARD_PREFIX}{stamp}.json"
    path.write_text(json.dumps(board, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def saved_boards(out: Path) -> list[Path]:
    """Los tableros guardados, del más viejo al más nuevo."""
    return sorted(out.glob(f"{BOARD_PREFIX}*.json"))


def load_board(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _fmt(value: Any, unit: str = "") -> str:
    if value is None:
        return "–"
    if isinstance(value, float):
        return f"{value:.3g}{unit}"
    return f"{value}{unit}"


def _ratio(part: Any, total: Any) -> float | None:
    return (
        part / total
        if isinstance(part, int) and isinstance(total, int) and total
        else None
    )


def compare_boards(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    """Qué cambió entre dos tableros, en palabras: una línea por número que importa."""
    lines = [f"antes {before['fecha']} · después {after['fecha']}"]

    def row(label: str, a: Any, b: Any, unit: str = "", lower_is_better: bool = False):
        mark = ""
        if isinstance(a, int | float) and isinstance(b, int | float) and a != b:
            better = (b < a) if lower_is_better else (b > a)
            mark = "  ↑ mejor" if better else "  ↓ peor"
        elif a == b:
            mark = "  igual"
        lines.append(f"{label}: {_fmt(a, unit)} → {_fmt(b, unit)}{mark}")

    row("acierto", before.get("acierto"), after.get("acierto"))
    for state in STATES:
        pa = (before.get("acierto_por_estado") or {}).get(state)
        pb = (after.get("acierto_por_estado") or {}).get(state)
        if pa is not None or pb is not None:
            row(f"  {state}", pa, pb)
    ea, eb = before.get("escenarios", {}), after.get("escenarios", {})
    ra = _ratio(ea.get("pasan"), ea.get("total"))
    rb = _ratio(eb.get("pasan"), eb.get("total"))
    mark = (
        "  igual"
        if ra == rb
        else ("  ↑ mejor" if (rb or 0) > (ra or 0) else "  ↓ peor")
    )
    lines.append(
        f"escenarios que pasan: {_fmt(ea.get('pasan'))} de {_fmt(ea.get('total'))}"
        f" → {_fmt(eb.get('pasan'))} de {_fmt(eb.get('total'))}{mark}"
    )
    row(
        "avisos falsos por hora",
        before.get("avisos_falsos_por_hora"),
        after.get("avisos_falsos_por_hora"),
        lower_is_better=True,
    )
    row(
        "latencia media",
        before.get("latencia_media_s"),
        after.get("latencia_media_s"),
        " s",
        lower_is_better=True,
    )
    ca, cb = before.get("costo") or {}, after.get("costo") or {}
    row(
        "costo por mesa por hora",
        ca.get("costo_mesa_hora_usd"),
        cb.get("costo_mesa_hora_usd"),
        " USD",
        lower_is_better=True,
    )
    row(
        "tokens de entrada por llamada",
        ca.get("tokens_entrada_por_llamada"),
        cb.get("tokens_entrada_por_llamada"),
        lower_is_better=True,
    )
    return lines


def print_board(board: dict[str, Any]) -> None:
    e = board["escenarios"]
    cost = board.get("costo") or {}
    print(f"Tablero de números · {board['fecha']}")
    print(
        f"  acierto por estado: {_fmt(board['acierto'])} sobre {board['platos']} platos"
        + (f" ({board['set']})" if board.get("set") else " (sin set etiquetado)")
    )
    for state, value in (board.get("acierto_por_estado") or {}).items():
        if value is not None:
            print(f"    {state}: {_fmt(value)}")
    print(f"  escenarios que pasan: {e['pasan']} de {e['total']}")
    for s in e["detalle"]:
        if not s["pasa"]:
            print(f"    ✗ {s['escenario']}: " + "; ".join(s["diferencias"]))
    print(
        f"  avisos falsos por hora: {_fmt(board['avisos_falsos_por_hora'])} ({board['avisos_falsos_nota']})"
    )
    print(f"  latencia media del modelo: {_fmt(board['latencia_media_s'], ' s')}")
    print(
        f"  costo por mesa por hora: {_fmt(cost.get('costo_mesa_hora_usd'), ' USD')}"
        + (f" ({cost['nota']})" if cost.get("nota") else "")
        + (
            f" · {cost['llamadas']} llamadas, {_fmt(cost.get('tokens_entrada_por_llamada'))} tokens de entrada por llamada"
            if cost.get("llamadas")
            else ""
        )
    )


# ---------------------------------------------------------------- línea de comandos


def _positive(raw: str) -> float:
    value = float(raw)
    if value <= 0:
        raise argparse.ArgumentTypeError("tiene que ser más de 0")
    return value


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Evaluación del modelo y tablero de números."
    )
    ap.add_argument("--set", type=Path, help="carpeta con labels.csv y los recortes")
    ap.add_argument("--all", action="store_true", help="el tablero completo, con fecha")
    ap.add_argument(
        "--sin-modelo", action="store_true", help="con --all: sin llamar al modelo"
    )
    ap.add_argument(
        "--comparar",
        nargs=2,
        type=Path,
        metavar="TABLERO",
        help="dos tableros guardados",
    )
    ap.add_argument("--por-llamada", type=int, default=8)
    ap.add_argument("--max", type=int, default=0, help="evaluar solo los primeros N")
    ap.add_argument(
        "--interval",
        type=_positive,
        default=30,
        help="cadencia del loop en segundos (más de 0), para el costo por hora",
    )
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    a = ap.parse_args(argv)
    utf8_console()
    logging.basicConfig(level=logging.WARNING)

    if a.comparar:
        for line in compare_boards(
            load_board(a.comparar[0]), load_board(a.comparar[1])
        ):
            print(line)
        return 0
    if not a.all and a.set is None:
        ap.error("falta --set (o --all, o --comparar)")

    set_dir = a.set
    if a.all and set_dir is None and (DEFAULT_SET / "labels.csv").exists():
        set_dir = DEFAULT_SET
    if set_dir is not None and not (set_dir / "labels.csv").exists():
        ap.error(f"{set_dir} no tiene labels.csv (ver tools/etiquetar.py)")

    if a.sin_modelo and not a.all:
        ap.error("--sin-modelo va con --all")

    scenarios: list[dict[str, Any]] = []
    if a.all:
        # antes que el modelo: armar el estimador lee el .env de esta máquina y lo
        # deja en el entorno, y los escenarios se corren con los umbrales de producción
        print("Escenarios por el pipeline entero…", flush=True)
        scenarios = run_scenarios()

    accuracy, results, latencies, cost = None, [], [], None
    if set_dir is not None and not a.sin_modelo:
        from servio_vision.llm import runtime

        estimator = make_estimator()
        results, latencies = evaluate_set(set_dir, estimator, a.por_llamada, a.max)
        accuracy = summarize(results)
        cost = cost_summary(runtime._meter, 3600 / a.interval)

    if not a.all:
        assert accuracy is not None  # hay --set y no hay --sin-modelo
        accuracy["latencia_media_s"] = (
            round(sum(latencies) / len(latencies), 1) if latencies else None
        )
        accuracy["platos_por_llamada"] = a.por_llamada
        a.out.mkdir(parents=True, exist_ok=True)
        with open(a.out / "resultados.json", "w", encoding="utf-8") as f:
            json.dump(
                {"resumen": accuracy, "platos": results},
                f,
                indent=2,
                ensure_ascii=False,
            )
        print(json.dumps(accuracy, indent=2, ensure_ascii=False))
        print(f"Detalle por plato en {a.out / 'resultados.json'}")
        return 0

    board = build_board(
        accuracy=accuracy,
        scenarios=scenarios,
        latencies=latencies,
        cost=cost,
        set_dir=set_dir,
        interval=a.interval,
    )
    previous = saved_boards(a.out)
    path = save_board(board, a.out)
    print_board(board)
    if previous:
        print(f"\nRespecto del anterior ({previous[-1].name}):")
        for line in compare_boards(load_board(previous[-1]), board):
            print("  " + line)
    print(f"\nGuardado en {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
