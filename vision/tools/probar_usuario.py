"""Levanta Servio entero para probarlo como un usuario (mozo, encargado, dueño).

Pensado para que una sesión de Claude Code (o cualquiera del equipo) lo corra con un
solo comando, en la nube o en la PC, y después revise lo que vio cada rol:

1. Sin `--source`, arma un video de prueba con N mesas (`out/prueba_usuario/mesas.mp4`)
   y sus zonas. Los platos del video se dibujan con el mismo guion que el clasificador
   `fake`, así lo que se ve coincide con lo que el sistema "lee".
2. Aparta el log y la base de corridas anteriores (`out/respaldo/<hora>/`), para que
   las métricas y el resumen sean solo de esta prueba más el historial simulado.
   Siempre usa el SQLite local, aunque el `.env` tenga `DATABASE_URL`.
3. Corre el servicio: `tools/vista_demo.py` sin ventana, grabando la vista de
   reconocimiento, con `--demo` y sin Telegram (los avisos salen por consola).
4. Arma lo que ve cada rol según `config/servio.toml` y `fixtures/restaurante/salon.json`:
   - mozo: fotos de la vista de reconocimiento y los avisos de **sus** mesas
     (`mozo_<nombre>.txt`);
   - encargado y dueño: `tools/metricas.py --rol ...` y el resumen de la corrida.
5. Chequea lo básico (hubo lecturas, avisos de cada tipo, cada aviso nombra al mozo
   de su mesa, la base tiene datos, el mozo no ve métricas…) y deja todo en
   `out/prueba_usuario/INFORME.md`. Sale con 1 si algún chequeo falla.

    python tools/probar_usuario.py                      # nube o PC: fake, 3 mesas
    python tools/probar_usuario.py --mesas 4 --lecturas 30
    python tools/probar_usuario.py --source "..\\video demo test.mp4" --classifier hybrid
    python tools/probar_usuario.py --conservar          # no apartar el log ni la base

Con `--source` y sin `--zones`, las mesas se detectan solas (hace falta
`requirements-yolo.txt`). Lo que va después de `--` se le pasa tal cual a la vista
(por ejemplo `-- --yolo-conf 0.07`).
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import unicodedata
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from servio_vision import VISION_DIR
from servio_vision.classifiers.fake import FakeClassifier
from servio_vision.restaurant import DEFAULT_DIR as RESTAURANT_DIR

OUT = VISION_DIR / "out"
TEST_DIR = OUT / "prueba_usuario"
LOG = OUT / "eventos.jsonl"
DB = OUT / "servio.db"
ROLES = ("mozo", "encargado", "dueno")
MAX_TABLES = 4  # salon.json tiene T1–T4

# video de prueba: 960×540, un frame por segundo (sobra: el fake no mira la imagen)
WIDTH, HEIGHT, FPS = 960, 540, 1
TABLE_COLOR = (45, 85, 130)  # BGR, madera
FOOD_COLOR = (40, 120, 200)
SNAPSHOTS = 6


def table_rects(n: int) -> list[tuple[int, int, int, int]]:
    """Rectángulos (x1, y1, x2, y2) de n mesas en una grilla de 2 columnas."""
    cols = 1 if n == 1 else 2
    rows = (n + cols - 1) // cols
    margin = 30
    w = (WIDTH - margin * (cols + 1)) // cols
    h = (HEIGHT - margin * (rows + 1)) // rows
    rects = []
    for i in range(n):
        r, c = divmod(i, cols)
        x1 = margin + c * (w + margin)
        y1 = margin + r * (h + margin)
        rects.append((x1, y1, x1 + w, y1 + h))
    return rects


def make_video(tables: int, readings: int, interval: float) -> tuple[Path, Path]:
    """Video de prueba y su zones.json. Cada lectura dura `interval` s de video."""
    TEST_DIR.mkdir(parents=True, exist_ok=True)
    video, zones_path = TEST_DIR / "mesas.mp4", TEST_DIR / "zones.json"
    rects = table_rects(tables)
    ids = [f"T{i + 1}" for i in range(tables)]
    zones = {
        "camera_id": "cam-prueba",
        "frame_size": [WIDTH, HEIGHT],
        "tables": [
            {
                "table_id": tid,
                "name": f"Mesa {i + 1}",
                "polygon": [[x1, y1], [x2, y1], [x2, y2], [x1, y2]],
            }
            for i, (tid, (x1, y1, x2, y2)) in enumerate(zip(ids, rects, strict=True))
        ],
    }
    zones_path.write_text(json.dumps(zones, indent=2), encoding="utf-8")

    script = FakeClassifier()  # el mismo guion que va a "leer" el pipeline
    writer = cv2.VideoWriter(
        str(video), cv2.VideoWriter_fourcc(*"mp4v"), FPS, (WIDTH, HEIGHT)
    )
    frames_per_reading = max(1, round(interval * FPS))
    # una lectura de más al final: el último frame leído tiene que existir
    for _ in range(readings + 1):
        frame = np.full((HEIGHT, WIDTH, 3), 70, np.uint8)
        for i, (tid, (x1, y1, x2, y2)) in enumerate(zip(ids, rects, strict=True)):
            cv2.rectangle(frame, (x1, y1), (x2, y2), TABLE_COLOR, -1)
            cv2.putText(
                frame,
                f"Mesa {i + 1}",
                (x1 + 8, y2 - 10),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                (230, 230, 230),
                1,
                cv2.LINE_AA,
            )
            report = script.classify(b"", tid, [])
            for item in report.items if report else []:
                bx, by, bw, bh = item.bbox
                tw, th = x2 - x1, y2 - y1
                center = (int(x1 + (bx + bw / 2) * tw), int(y1 + (by + bh / 2) * th))
                radius = int(min(bw * tw, bh * th) / 2)
                cv2.circle(frame, center, radius, (245, 245, 245), -1)
                food = int(radius * 0.8 * (item.fill_ratio or 0) ** 0.5)
                if food > 2:
                    cv2.circle(frame, center, food, FOOD_COLOR, -1)
        for _ in range(frames_per_reading):
            writer.write(frame)
    writer.release()
    return video, zones_path


def set_aside_previous() -> Path | None:
    """Mueve el log y la base de antes a out/respaldo/<hora>/ (no borra nada)."""
    old = [p for p in (LOG, DB) if p.exists()]
    if not old:
        return None
    backup = OUT / "respaldo" / datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    backup.mkdir(parents=True, exist_ok=True)
    for p in old:
        shutil.move(str(p), backup / p.name)
    return backup


def child_env() -> dict[str, str]:
    # emojis en la consola de Windows aunque la salida vaya a un archivo. Sin
    # DATABASE_URL: la prueba escribe en el SQLite local, nunca en el Postgres
    # compartido (vacía, el .env no la pisa y settings la toma como "sin valor")
    return {
        **os.environ,
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
        "DATABASE_URL": "",
    }


def run_tool(args: list[str], output: Path | None = None) -> tuple[int, str]:
    """Corre una herramienta de vision/ con este mismo Python; devuelve código y salida."""
    proc = subprocess.run(
        [sys.executable, *args],
        check=False,
        cwd=VISION_DIR,
        env=child_env(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    text = proc.stdout + proc.stderr
    if output is not None:
        output.write_text(text, encoding="utf-8")
    return proc.returncode, text


def run_service(cmd: list[str], console: Path) -> int:
    """El servicio con la salida a la terminal y a consola.txt a la vez."""
    with console.open("w", encoding="utf-8") as log:
        proc = subprocess.Popen(
            [sys.executable, *cmd],
            cwd=VISION_DIR,
            env=child_env(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            sys.stdout.write(line)
            log.write(line)
        return proc.wait()


def last_run(log: Path) -> list[dict]:
    """Eventos de la última corrida del log (desde el último run_started)."""
    if not log.exists():
        return []
    events = [
        json.loads(line)
        for line in log.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    starts = [i for i, e in enumerate(events) if e.get("event") == "run_started"]
    return events[starts[-1] :] if starts else events


def snapshots(video: Path, n: int = SNAPSHOTS) -> list[Path]:
    """n frames repartidos de la vista grabada, como .jpg para mirarlos."""
    cap = cv2.VideoCapture(str(video))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    saved = []
    for k in range(n):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(total * (k + 0.5) / n))
        ok, frame = cap.read()
        if ok:
            path = TEST_DIR / f"vista_{k + 1:02d}.jpg"
            cv2.imwrite(str(path), frame)
            saved.append(path)
    cap.release()
    return saved


def waiter_reports(
    events: list[dict], salon: dict
) -> tuple[dict[str, list[str]], list[str]]:
    """Avisos que le llegan a cada mozo (solo sus mesas) y los que nombran a otro."""
    by_waiter: dict[str, list[str]] = defaultdict(list)
    wrong = []
    names = {wid: w["nombre"] for wid, w in salon["mozos"].items()}
    for e in events:
        if not e.get("event", "").startswith("alert_"):
            continue
        table = salon["mesas"].get(e["table_id"], {})
        name = names.get(table.get("mozo"), "(sin mozo)")
        text = (e.get("text") or e.get("message", "")).replace("\n\n", " ")
        by_waiter[name].append(f"{e['ts'][11:19]} {e['event'][6:]:9} {text}")
        if (
            e["event"] == "alert_opened"
            and table
            and not e["message"].split("·")[0].strip().endswith(name)
        ):
            wrong.append(f"{e['table_id']}: {e['message']}")
    return by_waiter, wrong


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Levanta Servio y deja lo que ve cada rol en out/prueba_usuario/."
    )
    parser.add_argument(
        "--source", help="video propio (sin esto, se arma uno de prueba)"
    )
    parser.add_argument(
        "--classifier", default="fake", help="fake (default), yolo, hybrid…"
    )
    parser.add_argument("--zones", type=Path, help="zonas de las mesas (con --source)")
    parser.add_argument(
        "--mesas", type=int, default=3, help="mesas del video de prueba (1–4)"
    )
    parser.add_argument(
        "--lecturas", type=int, help="lecturas por mesa (default 20 + 3 por mesa)"
    )
    parser.add_argument(
        "--interval", type=float, default=30, help="segundos entre lecturas"
    )
    parser.add_argument(
        "--speed", type=float, default=60, help="velocidad del video (default 60×)"
    )
    parser.add_argument(
        "--conservar", action="store_true", help="no apartar el log y la base de antes"
    )
    args, extra = parser.parse_known_args(argv)
    extra = [a for a in extra if a != "--"]
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    if not 1 <= args.mesas <= MAX_TABLES:
        parser.error(f"--mesas va de 1 a {MAX_TABLES} (las mesas de salon.json)")
    readings = args.lecturas or 20 + 3 * (args.mesas - 1)

    if args.source and Path(args.source).resolve().is_relative_to(TEST_DIR):
        parser.error(f"--source no puede estar en {TEST_DIR}: se borra en cada prueba")
    if TEST_DIR.exists():
        shutil.rmtree(TEST_DIR)
    TEST_DIR.mkdir(parents=True)
    if args.source:
        # la vista corre desde vision/: las rutas relativas, desde donde se llamó
        local = Path(args.source)
        source = str(local.resolve()) if local.exists() else args.source  # cámara/URL
        zones = args.zones.resolve() if args.zones else None
    else:
        print(f"Armando video de prueba: {args.mesas} mesas, {readings} lecturas…")
        video, zones = make_video(args.mesas, readings, args.interval)
        source = str(video)
    backup = None if args.conservar else set_aside_previous()
    if backup:
        print(f"Log y base anteriores apartados en {backup}")

    view = TEST_DIR / "vista.mp4"
    cmd = [
        "tools/vista_demo.py", "--source", source, "--classifier", args.classifier,
        "--demo", "--no-telegram", "--interval", str(args.interval),
        "--speed", str(args.speed), "--limit", str(readings),
        "--no-window", "--record", str(view), *extra,
    ]  # fmt: skip
    if zones:
        cmd += ["--zones", str(zones)]
    print("Servicio:", "python", " ".join(cmd), "\n")
    code = run_service(cmd, TEST_DIR / "consola.txt")

    # ── lo que ve cada rol ──────────────────────────────────────────────────
    events = last_run(LOG)
    salon = json.loads((RESTAURANT_DIR / "salon.json").read_text(encoding="utf-8"))
    by_waiter, wrong_waiter = waiter_reports(events, salon)
    for name, lines in by_waiter.items():
        slug = (
            unicodedata.normalize("NFKD", name.lower())
            .encode("ascii", "ignore")
            .decode()
        )
        (TEST_DIR / f"mozo_{slug}.txt").write_text(
            f"Avisos que le llegan a {name} (solo sus mesas)\n\n"
            + "\n".join(lines)
            + "\n",
            encoding="utf-8",
        )
    shots = snapshots(view) if view.exists() else []
    role_codes = {
        rol: run_tool(
            ["tools/metricas.py", "--rol", rol], TEST_DIR / f"metricas_{rol}.txt"
        )[0]
        for rol in ROLES
    }
    run_tool(["tools/metricas.py", "--image", str(TEST_DIR / "metricas.png")])
    run_tool(
        ["tools/resumen.py", "--image", str(TEST_DIR / "resumen.png")],
        TEST_DIR / "resumen.txt",
    )

    # ── chequeos ────────────────────────────────────────────────────────────
    kinds = defaultdict(int)
    for e in events:
        kinds[e.get("event")] += 1
        if e.get("event") == "alert_opened":
            kinds[e["type"]] += 1
    db_rows = {}
    if DB.exists():
        import sqlite3

        with sqlite3.connect(DB) as conn:
            for t in ("platos_servidos", "avisos", "respuestas"):
                db_rows[t] = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    checks = [
        (code == 0, f"el servicio terminó bien (código {code})"),
        (kinds["observation"] > 0, f"{kinds['observation']} lecturas de mesa en el log"),
        (kinds["alert_opened"] > 0, f"{kinds['alert_opened']} avisos abiertos, {kinds['alert_resolved']} resueltos, {kinds['alert_reminder']} recordatorios"),
        (not wrong_waiter, "cada aviso nombra al mozo de su mesa" + (f": falla en {wrong_waiter}" if wrong_waiter else "")),
        (db_rows.get("platos_servidos", 0) > 0, f"la base tiene platos servidos ({db_rows})"),
        (role_codes["mozo"] == 3, "el mozo no ve métricas"),
        (role_codes["encargado"] == 0, "el encargado ve sus métricas"),
        (role_codes["dueno"] == 0, "el dueño ve sus métricas"),
        (len(shots) > 0, f"{len(shots)} fotos de la vista de reconocimiento"),
    ]  # fmt: skip
    if args.classifier == "fake":
        for kind in ("plato_lleno_mucho", "mesa_terminada"):
            checks.append((kinds[kind] > 0, f"hubo avisos de {kind} ({kinds[kind]})"))

    report = [
        "# Prueba de usuario de Servio",
        "",
        (
            f"{datetime.now().astimezone():%Y-%m-%d %H:%M} · fuente `{source}` · clasificador `{args.classifier}` · "
            f"{readings} lecturas cada {args.interval:g} s · umbrales de demo · sin Telegram"
        ),
        "",
        "## Chequeos",
        "",
        *[f"- {'✅' if ok else '❌'} {text}" for ok, text in checks],
        "",
        "## Qué mirar, por rol",
        "",
        (
            "- **Mozo:** `vista_*.jpg` (la vista de reconocimiento, todas las mesas) y "
            "`mozo_<nombre>.txt` (los avisos de sus mesas, como le llegarían al reloj). "
            "`metricas_mozo.txt` tiene que decir que no ve métricas."
        ),
        "- **Encargado:** `metricas_encargado.txt`, `resumen.txt` / `resumen.png`.",
        (
            "- **Dueño:** `metricas_dueno.txt`, `metricas.png` y la configuración en "
            "`config/servio.toml`."
        ),
        (
            "- **Todo junto:** `consola.txt` (lo que el servicio imprimió: cada aviso como "
            "saldría en Telegram) y `vista.mp4`."
        ),
        "",
        "## Archivos",
        "",
        *[f"- `{p.name}`" for p in sorted(TEST_DIR.iterdir())],
    ]
    if backup:
        report += ["", f"Log y base anteriores: `{backup}`"]
    (TEST_DIR / "INFORME.md").write_text("\n".join(report) + "\n", encoding="utf-8")

    print("\n" + "\n".join(report[4 : 6 + len(checks)]))
    print(f"\nTodo en {TEST_DIR} (empezá por INFORME.md)")
    return 0 if all(ok for ok, _ in checks) else 1


if __name__ == "__main__":
    sys.exit(main())
