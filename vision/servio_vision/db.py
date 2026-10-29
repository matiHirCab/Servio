"""Base de datos relacional de Servio (SQLite; en producción sería Postgres).

Guarda lo que pasa en el salón para sacar métricas con SQL:

    mozos (id, nombre)
    mesas (id, nombre, mozo_id → mozos)
    platos (id, nombre, precio)                           -- la carta
    respuestas_tipicas (plato_id → platos, orden, texto)  -- opciones del aviso
    platos_servidos (id, mesa_id → mesas, plato_id → platos, track_id, servido_at, origen,
                     ultimo_fill_ratio, retirado_at)  -- última lectura del plato
    estados_plato (id, plato_servido_id → platos_servidos, estado, fill_ratio, desde)
    avisos (id, tipo, mesa_id → mesas, mozo_id → mozos,
            plato_servido_id → platos_servidos, abierto_at, resuelto_at, origen)
    respuestas (id, aviso_id → avisos, mozo_id → mozos, respondido_por, texto,
                escrita, segundos, respondido_at)

`origen` distingue lo que pasó en vivo ("camara") del historial simulado
("historial"). Es un solo archivo (`vision/out/servio.db`), sin servidor; se puede
abrir con cualquier cliente de SQLite. Todas las escrituras van con un lock: el
loop y el hilo que escucha Telegram escriben a la vez.
"""

import json
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path

SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS mozos (
    id TEXT PRIMARY KEY,
    nombre TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS mesas (
    id TEXT PRIMARY KEY,
    nombre TEXT NOT NULL,
    mozo_id TEXT REFERENCES mozos(id)
);
CREATE TABLE IF NOT EXISTS platos (
    id TEXT PRIMARY KEY,
    nombre TEXT NOT NULL UNIQUE,
    precio REAL
);
CREATE TABLE IF NOT EXISTS respuestas_tipicas (
    plato_id TEXT NOT NULL REFERENCES platos(id),
    orden INTEGER NOT NULL,
    texto TEXT NOT NULL,
    PRIMARY KEY (plato_id, orden)
);
CREATE TABLE IF NOT EXISTS platos_servidos (
    id INTEGER PRIMARY KEY,
    mesa_id TEXT NOT NULL REFERENCES mesas(id),
    plato_id TEXT REFERENCES platos(id),
    track_id TEXT,
    servido_at TEXT NOT NULL,
    origen TEXT NOT NULL,
    ultimo_fill_ratio REAL,
    retirado_at TEXT
);
CREATE TABLE IF NOT EXISTS estados_plato (
    id INTEGER PRIMARY KEY,
    plato_servido_id INTEGER NOT NULL REFERENCES platos_servidos(id),
    estado TEXT NOT NULL,
    fill_ratio REAL,
    desde TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS avisos (
    id TEXT PRIMARY KEY,
    tipo TEXT NOT NULL,
    mesa_id TEXT NOT NULL REFERENCES mesas(id),
    mozo_id TEXT REFERENCES mozos(id),
    plato_servido_id INTEGER REFERENCES platos_servidos(id),
    abierto_at TEXT NOT NULL,
    resuelto_at TEXT,
    origen TEXT NOT NULL,
    motivo_cierre TEXT
);
CREATE TABLE IF NOT EXISTS respuestas (
    id INTEGER PRIMARY KEY,
    aviso_id TEXT NOT NULL REFERENCES avisos(id),
    mozo_id TEXT REFERENCES mozos(id),
    respondido_por TEXT,
    texto TEXT NOT NULL,
    escrita INTEGER NOT NULL,
    segundos REAL,
    respondido_at TEXT NOT NULL
);
"""

# Hora de inicio de cada servicio, para fechar el historial simulado.
SERVICE_START = {"almuerzo": "13:00:00", "cena": "21:00:00"}


class Database:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._migrate()

    def _migrate(self) -> None:
        """Bases creadas antes de `motivo_cierre`, `precio` o la última lectura
        del plato: se agregan las columnas. En el historial simulado, toda mesa
        terminada se cerró retirando los platos."""
        for table, column, kind in (
            ("platos", "precio", "REAL"),
            ("platos_servidos", "ultimo_fill_ratio", "REAL"),
            ("platos_servidos", "retirado_at", "TEXT"),
        ):
            existing = {
                r["name"] for r in self._conn.execute(f"PRAGMA table_info({table})")
            }
            if column not in existing:
                with self._conn:
                    self._conn.execute(
                        f"ALTER TABLE {table} ADD COLUMN {column} {kind}"
                    )
        columns = {r["name"] for r in self._conn.execute("PRAGMA table_info(avisos)")}
        if "motivo_cierre" in columns:
            return
        with self._conn:
            self._conn.execute("ALTER TABLE avisos ADD COLUMN motivo_cierre TEXT")
            self._conn.execute(
                "UPDATE avisos SET motivo_cierre = 'retirados'"
                " WHERE origen = 'historial' AND tipo = 'mesa_terminada'"
            )

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def _write(self, sql: str, params: tuple = ()) -> int | None:
        with self._lock, self._conn:
            return self._conn.execute(sql, params).lastrowid

    # --- carta, mesas y mozos (de la comanda) ---

    def load_restaurant(self, restaurant) -> None:
        with self._lock, self._conn:
            c = self._conn
            for wid, name in restaurant.waiters.items():
                c.execute("INSERT OR REPLACE INTO mozos VALUES (?, ?)", (wid, name))
            for tid, info in restaurant.tables.items():
                c.execute(
                    "INSERT OR REPLACE INTO mesas VALUES (?, ?, ?)",
                    (tid, f"Mesa {tid[1:]}", info["mozo"]),
                )
            for did, dish in restaurant.dishes.items():
                c.execute(
                    "INSERT OR REPLACE INTO platos VALUES (?, ?, ?)",
                    (did, dish.name, dish.price),
                )
                c.execute("DELETE FROM respuestas_tipicas WHERE plato_id = ?", (did,))
                for i, text in enumerate(dish.answers):
                    c.execute(
                        "INSERT INTO respuestas_tipicas VALUES (?, ?, ?)",
                        (did, i, text),
                    )

    def waiter_id(self, table_id: str) -> str | None:
        rows = self.query("SELECT mozo_id FROM mesas WHERE id = ?", (table_id,))
        return rows[0]["mozo_id"] if rows else None

    def _ensure_table(self, table_id: str) -> None:
        self._write(
            "INSERT OR IGNORE INTO mesas (id, nombre) VALUES (?, ?)",
            (table_id, f"Mesa {table_id[1:]}"),
        )

    # --- lo que pasa en vivo ---

    def served(
        self, table_id: str, dish_id: str | None, track_id: str, at: datetime
    ) -> int:
        self._ensure_table(table_id)
        return self._write(
            "INSERT INTO platos_servidos (mesa_id, plato_id, track_id, servido_at, origen)"
            " VALUES (?, ?, ?, ?, 'camara')",
            (table_id, dish_id, track_id, _iso(at)),
        )

    def plate_readings(
        self,
        readings: list[tuple[int, str, float | None, bool]],
        gone: list[int],
        at: datetime,
    ) -> None:
        """Lecturas de los platos de una mesa, en una sola transacción.

        `readings`: (plato servido, estado, fill_ratio, cambió de estado). Guarda
        el último fill_ratio (vacío si la lectura no lo trae: no se arrastra uno
        viejo) y, si cambió de estado, desde cuándo (ritmo por plato). Un plato
        que se ve otra vez deja de estar retirado.
        `gone`: platos que se fueron de la mesa en esta lectura (sobra por plato).
        """
        ts = _iso(at)
        with self._lock, self._conn:
            c = self._conn
            for served_id, state, fill_ratio, changed in readings:
                c.execute(
                    "UPDATE platos_servidos SET ultimo_fill_ratio = ?,"
                    " retirado_at = NULL WHERE id = ?",
                    (fill_ratio, served_id),
                )
                if changed:
                    c.execute(
                        "INSERT INTO estados_plato (plato_servido_id, estado,"
                        " fill_ratio, desde) VALUES (?, ?, ?, ?)",
                        (served_id, state, fill_ratio, ts),
                    )
            for served_id in gone:
                c.execute(
                    "UPDATE platos_servidos SET retirado_at = ? WHERE id = ?",
                    (ts, served_id),
                )

    def alert_opened(
        self,
        alert_id: str,
        kind: str,
        table_id: str,
        served_id: int | None,
        at: datetime,
    ) -> None:
        self._ensure_table(table_id)
        self._write(
            "INSERT OR REPLACE INTO avisos (id, tipo, mesa_id, mozo_id, plato_servido_id,"
            " abierto_at, origen) VALUES (?, ?, ?, ?, ?, ?, 'camara')",
            (alert_id, kind, table_id, self.waiter_id(table_id), served_id, _iso(at)),
        )

    def alert_resolved(
        self, alert_id: str, at: datetime, reason: str | None = None
    ) -> None:
        """`reason`, para mesa terminada: "retirados" o "servido" (llegó algo nuevo)."""
        self._write(
            "UPDATE avisos SET resuelto_at = ?, motivo_cierre = ? WHERE id = ?",
            (_iso(at), reason, alert_id),
        )

    def feedback(self, event: dict) -> None:
        self._write(
            "INSERT INTO respuestas (aviso_id, mozo_id, respondido_por, texto, escrita,"
            " segundos, respondido_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                event["alert_id"],
                self.waiter_id(event["table_id"]),
                event.get("answered_by"),
                event["answer"],
                int(bool(event.get("free_text"))),
                event.get("seconds_to_answer"),
                event["ts"],
            ),
        )

    # --- historial simulado ---

    def import_history(self, path: Path, restaurant) -> int:
        """Carga `historial.jsonl` (una sola vez: si ya está, no hace nada)."""
        if self.query("SELECT 1 FROM avisos WHERE origen = 'historial' LIMIT 1"):
            return 0
        dish_ids = {d.name: did for did, d in restaurant.dishes.items()}
        waiter_ids = {name: wid for wid, name in restaurant.waiters.items()}
        count = 0
        clock: dict[tuple, datetime] = {}
        last_served: dict[tuple, int] = {}
        with self._lock, self._conn:
            c = self._conn
            for n, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
                r = json.loads(line)
                key = (r["fecha"], r["servicio"], r["mesa"])
                start = datetime.fromisoformat(
                    f"{r['fecha']}T{SERVICE_START[r['servicio']]}"
                )
                at = clock.get(key, start) + timedelta(minutes=4)
                clock[key] = at
                waiter = waiter_ids.get(r["mozo"])
                if r["tipo"] == "servido":
                    cur = c.execute(
                        "INSERT INTO platos_servidos (mesa_id, plato_id, servido_at, origen)"
                        " VALUES (?, ?, ?, 'historial')",
                        (r["mesa"], dish_ids.get(r["plato"]), _iso(at)),
                    )
                    last_served[(key, r["plato"])] = cur.lastrowid
                elif r["tipo"] == "plato_lleno":
                    alert_id = f"h{n}"
                    c.execute(
                        "INSERT INTO avisos (id, tipo, mesa_id, mozo_id, plato_servido_id,"
                        " abierto_at, resuelto_at, origen)"
                        " VALUES (?, 'plato_lleno_mucho', ?, ?, ?, ?, ?, 'historial')",
                        (
                            alert_id,
                            r["mesa"],
                            waiter,
                            last_served.get((key, r["plato"])),
                            _iso(at),
                            _iso(at + timedelta(minutes=5)),
                        ),
                    )
                    c.execute(
                        "INSERT INTO respuestas (aviso_id, mozo_id, respondido_por, texto,"
                        " escrita, segundos, respondido_at) VALUES (?, ?, ?, ?, 0, ?, ?)",
                        (
                            alert_id,
                            waiter,
                            r["mozo"],
                            r["respuesta"],
                            r["segundos_respuesta"],
                            _iso(at + timedelta(seconds=r["segundos_respuesta"])),
                        ),
                    )
                    count += 1
                elif r["tipo"] == "mesa_terminada":
                    c.execute(
                        "INSERT INTO avisos (id, tipo, mesa_id, mozo_id, abierto_at,"
                        " resuelto_at, origen, motivo_cierre)"
                        " VALUES (?, 'mesa_terminada', ?, ?, ?, ?, 'historial',"
                        " 'retirados')",
                        (
                            f"h{n}",
                            r["mesa"],
                            waiter,
                            _iso(at),
                            _iso(at + timedelta(minutes=r["minutos_hasta_atender"])),
                        ),
                    )
                    count += 1
        return count


def _iso(at: datetime) -> str:
    return at.isoformat().replace("+00:00", "Z")
