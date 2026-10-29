"""Base de datos relacional de Servio (A-6): SQLite local o Postgres compartido.

Guarda lo que pasa en el salón para sacar métricas con SQL:

    mozos (id, nombre)
    mesas (id, nombre, mozo_id → mozos)
    platos (id, nombre, precio)                           -- la carta
    respuestas_tipicas (plato_id → platos, orden, texto)  -- opciones del aviso
    platos_servidos (id, local_id, mesa_id → mesas, plato_id → platos, track_id,
                     servido_at, origen, ultimo_fill_ratio, retirado_at)
    estados_plato (id, plato_servido_id → platos_servidos, estado, fill_ratio, desde)
    avisos (local_id, id, tipo, mesa_id → mesas, mozo_id → mozos,
            plato_servido_id → platos_servidos, abierto_at, resuelto_at, origen,
            motivo_cierre)                                -- clave (local_id, id)
    respuestas (id, local_id, aviso_id → avisos(local_id, id), mozo_id → mozos,
                respondido_por, texto, escrita, segundos, respondido_at)

- `local_id`: cada casa (cada local) escribe con el suyo en la misma base, así dos
  casas con una "Mesa 1" no se pisan los avisos (el id de un aviso es
  `{mesa}-{tipo}-{segundo}`, único solo dentro de un local).
- `origen` distingue lo que pasó en vivo ("camara") del historial simulado
  ("historial").
- `mozos`, `mesas`, `platos` y `respuestas_tipicas` (la comanda) son **compartidas**
  entre casas: hoy todas cargan el mismo `fixtures/restaurante/`. Si dos casas
  tuvieran cartas o mozos distintos, la última en arrancar pisaría a la otra; eso
  es separar la comanda por local (D-2, alta del local desde la web), no esta tarea.
- Sin `DATABASE_URL` es un archivo SQLite (`vision/out/servio.db`), sin servidor: para
  los tests y para trabajar sin internet. Con `DATABASE_URL` (Neon o Supabase, en el
  `.env`) todas las casas escriben en el mismo Postgres y el dashboard lee de ahí.
- El esquema está versionado con Alembic (`servio_vision/migrations/`): al abrir la
  base se aplican las migraciones que falten, así se agregan columnas sin borrar
  datos. Una base de antes de Alembic se reconoce por sus tablas y se marca en la
  versión que le corresponde antes de actualizarla.
- Todas las escrituras van con un lock: el loop y el hilo que escucha Telegram
  escriben a la vez.
"""

import json
import logging
import threading
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Self

import sqlalchemy as sa
from alembic import command
from alembic.config import Config as AlembicConfig
from sqlalchemy import (
    Column,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    MetaData,
    String,
    Table,
)

from servio_vision import DEFAULT_LOCAL, VISION_DIR

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
DEFAULT_PATH = VISION_DIR / "out" / "servio.db"
HISTORY_LOCAL = "historial"  # el historial simulado no es de ninguna casa
HISTORY_LOCK = 4211  # pg_advisory_xact_lock: una sola casa importa el historial
MIGRATE_LOCK = 4212  # pg_advisory_lock: una sola casa migra a la vez
HISTORY_EXISTS_SQL = "SELECT 1 FROM avisos WHERE origen = 'historial' LIMIT 1"

# Hora de inicio de cada servicio, para fechar el historial simulado.
SERVICE_START = {"almuerzo": "13:00:00", "cena": "21:00:00"}

# El esquema vigente (la última migración lo deja así). Las fechas van como texto
# ISO con "Z": lo mismo que el log de eventos, y se compara bien como texto.
metadata = MetaData()
mozos = Table(
    "mozos",
    metadata,
    Column("id", String, primary_key=True),
    Column("nombre", String, nullable=False),
)
mesas = Table(
    "mesas",
    metadata,
    Column("id", String, primary_key=True),
    Column("nombre", String, nullable=False),
    Column("mozo_id", String, ForeignKey("mozos.id")),
)
platos = Table(
    "platos",
    metadata,
    Column("id", String, primary_key=True),
    Column("nombre", String, nullable=False, unique=True),
    Column("precio", Float),  # en pesos; sin precio no entra en el desperdicio
)
respuestas_tipicas = Table(
    "respuestas_tipicas",
    metadata,
    Column("plato_id", String, ForeignKey("platos.id"), primary_key=True),
    Column("orden", Integer, primary_key=True),
    Column("texto", String, nullable=False),
)
platos_servidos = Table(
    "platos_servidos",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("local_id", String, nullable=False, server_default=DEFAULT_LOCAL),
    Column("mesa_id", String, ForeignKey("mesas.id"), nullable=False),
    Column("plato_id", String, ForeignKey("platos.id")),
    Column("track_id", String),
    Column("servido_at", String, nullable=False),
    Column("origen", String, nullable=False),
    # la última lectura del plato y cuándo se fue de la mesa (métricas de M-1)
    Column("ultimo_fill_ratio", Float),
    Column("retirado_at", String),
)
estados_plato = Table(
    "estados_plato",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column(
        "plato_servido_id", Integer, ForeignKey("platos_servidos.id"), nullable=False
    ),
    Column("estado", String, nullable=False),
    Column("fill_ratio", Float),
    Column("desde", String, nullable=False),
)
avisos = Table(
    "avisos",
    metadata,
    Column("local_id", String, primary_key=True, server_default=DEFAULT_LOCAL),
    Column("id", String, primary_key=True),
    Column("tipo", String, nullable=False),
    Column("mesa_id", String, ForeignKey("mesas.id"), nullable=False),
    Column("mozo_id", String, ForeignKey("mozos.id")),
    Column("plato_servido_id", Integer, ForeignKey("platos_servidos.id")),
    Column("abierto_at", String, nullable=False),
    Column("resuelto_at", String),
    Column("origen", String, nullable=False),
    Column("motivo_cierre", String),
)
respuestas = Table(
    "respuestas",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("local_id", String, nullable=False, server_default=DEFAULT_LOCAL),
    Column("aviso_id", String, nullable=False),
    Column("mozo_id", String, ForeignKey("mozos.id")),
    Column("respondido_por", String),
    Column("texto", String, nullable=False),
    Column("escrita", Integer, nullable=False),
    Column("segundos", Float),
    Column("respondido_at", String, nullable=False),
    ForeignKeyConstraint(["local_id", "aviso_id"], ["avisos.local_id", "avisos.id"]),
)


class Row(tuple):
    """Una fila: tupla (como `sqlite3.Row`, `tuple(row)` da los valores) que también
    se indexa por nombre de columna (`row["mozo"]`)."""

    def __new__(cls, values: Sequence[Any], keys: Sequence[str]) -> Self:
        row = super().__new__(cls, values)
        row._keys = tuple(keys)
        return row

    def __getitem__(self, key):
        if isinstance(key, str):
            if key not in self._keys:
                raise KeyError(key)
            return tuple.__getitem__(self, self._keys.index(key))
        return tuple.__getitem__(self, key)

    def keys(self) -> tuple[str, ...]:
        return self._keys


def engine_url(path: Path | None, url: str | None) -> str:
    """La URL de SQLAlchemy: `url` (Postgres) o el archivo SQLite `path`. Un
    `postgresql://` pelado va con el driver psycopg (el 3)."""
    if url:
        for prefix in ("postgresql://", "postgres://"):
            if url.startswith(prefix):
                return "postgresql+psycopg://" + url[len(prefix) :]
        return url
    return f"sqlite:///{path or DEFAULT_PATH}"


class Database:
    def __init__(
        self,
        path: Path | None = None,
        url: str | None = None,
        local_id: str = DEFAULT_LOCAL,
    ) -> None:
        """`path`: archivo SQLite (y, con `url`, la carpeta `out/` de esta máquina,
        donde van las imágenes de métricas). `url`: Postgres compartido
        (`DATABASE_URL`). `local_id`: con qué nombre escribe esta casa."""
        self.path = path or DEFAULT_PATH
        self.local_id = local_id
        self._lock = threading.Lock()
        target = engine_url(path, url)
        if target.startswith("sqlite"):
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # un solo archivo, varios hilos (el loop y el de Telegram) con el lock
            self._engine = sa.create_engine(
                target, connect_args={"check_same_thread": False}
            )
            sa.event.listen(self._engine, "connect", _sqlite_foreign_keys_on)
        else:
            try:
                self._engine = sa.create_engine(target, pool_pre_ping=True)
            except ModuleNotFoundError as e:
                raise RuntimeError(
                    "Para DATABASE_URL hace falta el driver de Postgres: "
                    "pip install -r requirements-postgres.txt"
                ) from e
        self.dialect = self._engine.dialect.name
        with self._lock:
            self._upgrade()

    # --- esquema ---

    def _upgrade(self) -> None:
        """Aplica las migraciones que falten. Una base de antes de Alembic (sin
        `alembic_version` pero con `avisos`) se marca en la versión que ya tiene."""
        cfg = AlembicConfig()
        cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
        with self._engine.connect() as conn:
            if self.dialect == "sqlite":
                # batch de tablas (copiar y renombrar) sin que las FK lo frenen
                conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
            elif self.dialect == "postgresql":
                # dos casas arrancando a la vez: migra una, la otra espera y ya
                # encuentra la base al día (lock de sesión, se suelta al final)
                conn.execute(sa.text(f"SELECT pg_advisory_lock({MIGRATE_LOCK})"))
                conn.commit()
            try:
                cfg.attributes["connection"] = conn
                inspector = sa.inspect(conn)
                tables = set(inspector.get_table_names())
                if "alembic_version" not in tables and "avisos" in tables:
                    columns = {c["name"] for c in inspector.get_columns("avisos")}
                    command.stamp(cfg, "0002" if "motivo_cierre" in columns else "0001")
                command.upgrade(cfg, "head")
                conn.commit()
            finally:
                if self.dialect == "sqlite":
                    # la conexión vuelve al pool: que no quede con las FK apagadas
                    conn.exec_driver_sql("PRAGMA foreign_keys=ON")
                elif self.dialect == "postgresql":
                    # si la migración falló, la transacción quedó abortada: sin el
                    # rollback el unlock también fallaría y el lock quedaría tomado
                    conn.rollback()
                    conn.execute(sa.text(f"SELECT pg_advisory_unlock({MIGRATE_LOCK})"))
                    conn.commit()

    def close(self) -> None:
        with self._lock:
            self._engine.dispose()

    # --- SQL a mano (métricas, tests) ---

    def query(self, sql: str, params: Mapping[str, Any] | None = None) -> list[Row]:
        """Consulta de lectura, con parámetros con nombre (`WHERE id = :mesa`). Sin el
        lock: las lecturas (seis por `/metricas`, desde el hilo de Telegram) no
        tienen que frenar las escrituras del loop; el pool da una conexión a cada
        hilo."""
        statement, bound = _bind(sql, params)
        with self._engine.connect() as conn:
            result = conn.execute(statement, bound)
            keys = tuple(result.keys())  # una sola vez, compartida por las filas
            return [Row(tuple(r), keys) for r in result]

    def render(self, template: str) -> str:
        """Rellena en una consulta lo que cambia según el motor: cuánto tardó un aviso
        (`{elapsed_minutes}` / `{elapsed_seconds}` entre `a.abierto_at` y
        `a.resuelto_at`). SQLite y Postgres restan fechas distinto."""
        # reemplazo literal, no str.format: una llave suelta en el SQL no es un error
        return template.replace(
            "{elapsed_minutes}", self.elapsed("a.abierto_at", "a.resuelto_at", 60)
        ).replace("{elapsed_seconds}", self.elapsed("a.abierto_at", "a.resuelto_at", 1))

    def elapsed(self, start: str, end: str, unit_seconds: int) -> str:
        """Expresión SQL: segundos (o minutos, con 60) entre dos columnas ISO."""
        if self.dialect == "postgresql":
            seconds = f"EXTRACT(EPOCH FROM ({end}::timestamptz - {start}::timestamptz))"
        else:
            seconds = f"(julianday({end}) - julianday({start})) * 86400"
        return seconds if unit_seconds == 1 else f"({seconds}) / {unit_seconds}"

    def _write(self, statement, params: Mapping[str, Any] | None = None):
        with self._lock, self._engine.begin() as conn:
            return conn.execute(statement, params or {})

    def _upsert(self, table: Table):
        """`INSERT ... ON CONFLICT` del motor que sea (antes `INSERT OR REPLACE`)."""
        if self.dialect == "postgresql":
            from sqlalchemy.dialects.postgresql import insert
        else:
            from sqlalchemy.dialects.sqlite import insert
        return insert(table)

    # --- carta, mesas y mozos (de la comanda) ---

    def load_restaurant(self, restaurant) -> None:
        with self._lock, self._engine.begin() as conn:
            for wid, name in restaurant.waiters.items():
                row = {"id": wid, "nombre": name}
                conn.execute(
                    self._upsert(mozos)
                    .values(row)
                    .on_conflict_do_update(index_elements=["id"], set_=row)
                )
            for tid, info in restaurant.tables.items():
                row = {"id": tid, "nombre": f"Mesa {tid[1:]}", "mozo_id": info["mozo"]}
                conn.execute(
                    self._upsert(mesas)
                    .values(row)
                    .on_conflict_do_update(index_elements=["id"], set_=row)
                )
            for did, dish in restaurant.dishes.items():
                row = {"id": did, "nombre": dish.name, "precio": dish.price}
                conn.execute(
                    self._upsert(platos)
                    .values(row)
                    .on_conflict_do_update(index_elements=["id"], set_=row)
                )
                # upsert y no DELETE + INSERT: dos casas arrancando a la vez no se
                # pisan la clave (plato_id, orden); las opciones que sobran se borran
                for i, text in enumerate(dish.answers):
                    row = {"plato_id": did, "orden": i, "texto": text}
                    conn.execute(
                        self._upsert(respuestas_tipicas)
                        .values(row)
                        .on_conflict_do_update(
                            index_elements=["plato_id", "orden"], set_=row
                        )
                    )
                conn.execute(
                    respuestas_tipicas.delete().where(
                        respuestas_tipicas.c.plato_id == did,
                        respuestas_tipicas.c.orden >= len(dish.answers),
                    )
                )

    def waiter_id(self, table_id: str) -> str | None:
        rows = self.query(
            "SELECT mozo_id FROM mesas WHERE id = :mesa", {"mesa": table_id}
        )
        return rows[0]["mozo_id"] if rows else None

    def _ensure_table(self, table_id: str):
        """La mesa, si no está en la comanda (INSERT OR IGNORE). Se ejecuta en la
        misma transacción que la escritura que la necesita."""
        return (
            self._upsert(mesas)
            .values(id=table_id, nombre=f"Mesa {table_id[1:]}")
            .on_conflict_do_nothing(index_elements=["id"])
        )

    def _waiter_of(self, table_id: str):
        """El mozo de la mesa, como subconsulta: una ida y vuelta menos."""
        return (
            sa.select(mesas.c.mozo_id).where(mesas.c.id == table_id).scalar_subquery()
        )

    # --- lo que pasa en vivo ---
    # Una sola transacción por escritura: con un Postgres remoto, cada ida y vuelta
    # bajo el lock frena el loop de la cámara y el hilo de Telegram.

    def served(
        self, table_id: str, dish_id: str | None, track_id: str, at: datetime
    ) -> int:
        with self._lock, self._engine.begin() as conn:
            conn.execute(self._ensure_table(table_id))
            result = conn.execute(
                platos_servidos.insert().values(
                    local_id=self.local_id,
                    mesa_id=table_id,
                    plato_id=dish_id,
                    track_id=track_id,
                    servido_at=_iso(at),
                    origen="camara",
                )
            )
            return result.inserted_primary_key[0]

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
        if not readings and not gone:
            return
        ts = _iso(at)
        ps = platos_servidos.c
        with self._lock, self._engine.begin() as conn:
            for served_id, state, fill_ratio, changed in readings:
                conn.execute(
                    platos_servidos.update()
                    .where(ps.id == served_id)
                    .values(ultimo_fill_ratio=fill_ratio, retirado_at=None)
                )
                if changed:
                    conn.execute(
                        estados_plato.insert().values(
                            plato_servido_id=served_id,
                            estado=state,
                            fill_ratio=fill_ratio,
                            desde=ts,
                        )
                    )
            if gone:
                conn.execute(
                    platos_servidos.update()
                    .where(ps.id.in_(gone))
                    .values(retirado_at=ts)
                )

    def alert_opened(
        self,
        alert_id: str,
        kind: str,
        table_id: str,
        served_id: int | None,
        at: datetime,
    ) -> None:
        row = {
            "local_id": self.local_id,
            "id": alert_id,
            "tipo": kind,
            "mesa_id": table_id,
            "mozo_id": self._waiter_of(table_id),
            "plato_servido_id": served_id,
            "abierto_at": _iso(at),
            "resuelto_at": None,
            "origen": "camara",
            "motivo_cierre": None,
        }
        with self._lock, self._engine.begin() as conn:
            conn.execute(self._ensure_table(table_id))
            conn.execute(
                self._upsert(avisos)
                .values(row)
                .on_conflict_do_update(index_elements=["local_id", "id"], set_=row)
            )

    def alert_resolved(
        self, alert_id: str, at: datetime, reason: str | None = None
    ) -> None:
        """`reason`, para mesa terminada: "retirados" o "servido" (llegó algo nuevo)."""
        self._write(
            avisos.update()
            .where(avisos.c.local_id == self.local_id, avisos.c.id == alert_id)
            .values(resuelto_at=_iso(at), motivo_cierre=reason)
        )

    def feedback(self, event: dict) -> None:
        self._write(
            respuestas.insert().values(
                local_id=self.local_id,
                aviso_id=event["alert_id"],
                mozo_id=self._waiter_of(event["table_id"]),
                respondido_por=event.get("answered_by"),
                texto=event["answer"],
                escrita=int(bool(event.get("free_text"))),
                segundos=event.get("seconds_to_answer"),
                respondido_at=event["ts"],
            )
        )

    # --- historial simulado ---

    def import_history(self, path: Path, restaurant) -> int:
        """Carga `historial.jsonl` (una sola vez: si ya está, no hace nada). Va con
        `local_id` "historial": no es de ninguna casa.

        Tres inserts masivos y no uno por fila: en un Postgres remoto son miles de
        idas y vueltas con el lock tomado (el loop de la cámara esperaría minutos).
        En Postgres, además, un lock consultivo: dos casas que piden `/metricas` a la
        vez no importan las dos."""
        if self.query(HISTORY_EXISTS_SQL):
            return 0
        served_rows, served_keys, alert_rows, answer_rows = _history_rows(
            path, restaurant
        )
        with self._lock, self._engine.begin() as conn:
            if self.dialect == "postgresql":
                conn.execute(sa.text(f"SELECT pg_advisory_xact_lock({HISTORY_LOCK})"))
            if conn.execute(sa.text(HISTORY_EXISTS_SQL)).first():  # la otra llegó antes
                return 0
            served_ids: dict = {}
            if served_rows:  # un insert vacío no es "nada": es una fila por defecto
                ids = conn.execute(
                    platos_servidos.insert().returning(
                        platos_servidos.c.id, sort_by_parameter_order=True
                    ),
                    served_rows,
                ).scalars()
                served_ids = dict(zip(served_keys, ids, strict=True))
            for alert in alert_rows:
                alert["plato_servido_id"] = served_ids.get(alert.pop("served_key"))
            if alert_rows:
                conn.execute(avisos.insert(), alert_rows)
            if answer_rows:
                conn.execute(respuestas.insert(), answer_rows)
        return len(alert_rows)


def _history_rows(
    path: Path, restaurant
) -> tuple[list[dict], list, list[dict], list[dict]]:
    """Las filas del historial simulado, listas para insertar: platos servidos (y la
    clave con la que los avisos los referencian), avisos y respuestas."""
    dish_ids = {d.name: did for did, d in restaurant.dishes.items()}
    waiter_ids = {name: wid for wid, name in restaurant.waiters.items()}
    clock: dict[tuple, datetime] = {}
    served_rows: list[dict] = []
    served_keys: list[tuple] = []
    last_served: dict[tuple, tuple] = {}  # (mesa y servicio, plato) → su clave
    alert_rows: list[dict] = []
    answer_rows: list[dict] = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
        r = json.loads(line)
        key = (r["fecha"], r["servicio"], r["mesa"])
        start = datetime.fromisoformat(f"{r['fecha']}T{SERVICE_START[r['servicio']]}")
        at = clock.get(key, start) + timedelta(minutes=4)
        clock[key] = at
        waiter = waiter_ids.get(r["mozo"])
        if r["tipo"] == "servido":
            served_rows.append(
                {
                    "local_id": HISTORY_LOCAL,
                    "mesa_id": r["mesa"],
                    "plato_id": dish_ids.get(r["plato"]),
                    "track_id": None,
                    "servido_at": _iso(at),
                    "origen": "historial",
                }
            )
            served_keys.append((n, key, r["plato"]))
            last_served[(key, r["plato"])] = served_keys[-1]
        elif r["tipo"] == "plato_lleno":
            # el último servido de ese plato en esa mesa y servicio
            served_key = last_served.get((key, r["plato"]))
            alert_rows.append(
                {
                    "local_id": HISTORY_LOCAL,
                    "id": f"h{n}",
                    "tipo": "plato_lleno_mucho",
                    "mesa_id": r["mesa"],
                    "mozo_id": waiter,
                    "served_key": served_key,
                    "abierto_at": _iso(at),
                    "resuelto_at": _iso(at + timedelta(minutes=5)),
                    "origen": "historial",
                    "motivo_cierre": None,
                }
            )
            answer_rows.append(
                {
                    "local_id": HISTORY_LOCAL,
                    "aviso_id": f"h{n}",
                    "mozo_id": waiter,
                    "respondido_por": r["mozo"],
                    "texto": r["respuesta"],
                    "escrita": 0,
                    "segundos": r["segundos_respuesta"],
                    "respondido_at": _iso(
                        at + timedelta(seconds=r["segundos_respuesta"])
                    ),
                }
            )
        elif r["tipo"] == "mesa_terminada":
            alert_rows.append(
                {
                    "local_id": HISTORY_LOCAL,
                    "id": f"h{n}",
                    "tipo": "mesa_terminada",
                    "mesa_id": r["mesa"],
                    "mozo_id": waiter,
                    "served_key": None,
                    "abierto_at": _iso(at),
                    "resuelto_at": _iso(
                        at + timedelta(minutes=r["minutos_hasta_atender"])
                    ),
                    "origen": "historial",
                    "motivo_cierre": "retirados",
                }
            )
    return served_rows, served_keys, alert_rows, answer_rows


def _sqlite_foreign_keys_on(dbapi_connection, _record) -> None:
    dbapi_connection.execute("PRAGMA foreign_keys=ON")


def _bind(sql: str, params: Mapping[str, Any] | None):
    """`text()` con parámetros con nombre. Posicionales (`?`) no: un `?` adentro de
    un literal ('%?%') haría cualquier cosa."""
    if params is None:
        return sa.text(sql), {}
    if not isinstance(params, Mapping):
        raise TypeError(
            "params: un dict con nombres (`WHERE id = :mesa`), no posicionales"
        )
    return sa.text(sql), dict(params)


def _iso(at: datetime) -> str:
    return at.isoformat().replace("+00:00", "Z")
