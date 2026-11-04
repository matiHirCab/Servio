"""Las migraciones de Alembic: una sola cabeza, suben, bajan y vuelven a subir en
SQLite (`servio_vision/migrations/`)."""

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory

from servio_vision.db import MIGRATIONS_DIR, Database, metadata


def alembic_config() -> AlembicConfig:
    cfg = AlembicConfig()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    return cfg


@pytest.fixture
def engine_for(tmp_path):
    """Engines de SQLite que se cierran al terminar el test (en Windows el archivo
    quedaría abierto)."""
    engines = []

    def make(name):
        engine = sa.create_engine(f"sqlite:///{tmp_path / name}")
        engines.append(engine)
        return engine

    yield make
    for engine in engines:
        engine.dispose()


def run(engine, action, *args):
    """Corre un comando de Alembic como `Database._upgrade`: sobre una conexión, con
    las FK apagadas (el batch de SQLite copia y renombra tablas)."""
    cfg = alembic_config()
    with engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
        cfg.attributes["connection"] = conn
        action(cfg, *args)
        conn.commit()


def tables(engine) -> set[str]:
    return set(sa.inspect(engine).get_table_names()) - {"alembic_version"}


def test_history_has_a_single_head():
    heads = ScriptDirectory.from_config(alembic_config()).get_heads()
    assert len(heads) == 1


def test_revisions_form_a_linear_chain_from_base():
    script = ScriptDirectory.from_config(alembic_config())
    revisions = list(script.walk_revisions())
    assert [r.down_revision for r in revisions].count(None) == 1
    assert all(isinstance(r.down_revision, str | None) for r in revisions)


def test_upgrade_head_creates_every_model_table(engine_for):
    engine = engine_for("a.db")
    run(engine, command.upgrade, "head")
    assert set(metadata.tables) <= tables(engine)


def schema(engine) -> dict[str, set[str]]:
    """Tabla → columnas."""
    inspector = sa.inspect(engine)
    return {t: {c["name"] for c in inspector.get_columns(t)} for t in tables(engine)}


def test_last_migration_downgrades_and_upgrades_again(engine_for):
    """Si la última migración se puede deshacer, al volver a subir el esquema
    (tablas y columnas) queda igual. Una irreversible a propósito se saltea."""
    engine = engine_for("b.db")
    run(engine, command.upgrade, "head")
    full = schema(engine)

    try:
        run(engine, command.downgrade, "-1")
    except NotImplementedError:
        pytest.skip("la última migración es irreversible a propósito")

    run(engine, command.upgrade, "head")
    assert schema(engine) == full


def test_local_id_migration_refuses_to_go_back(engine_for):
    """0003 no se deshace a propósito: habría que elegir qué local conservar."""
    engine = engine_for("c.db")
    run(engine, command.upgrade, "0003")
    with pytest.raises(NotImplementedError, match="local_id"):
        run(engine, command.downgrade, "0002")


def test_database_reopened_stays_at_head_and_keeps_rows(tmp_path):
    path = tmp_path / "servio.db"
    first = Database(path)
    with first._engine.begin() as conn:
        conn.execute(
            sa.text("INSERT INTO platos (id, nombre) VALUES ('donas', 'Donas')")
        )
    first.close()
    second = Database(path)
    try:
        assert [tuple(r) for r in second.query("SELECT id, nombre FROM platos")] == [
            ("donas", "Donas")
        ]
        [(version,)] = second.query("SELECT version_num FROM alembic_version")
        head = ScriptDirectory.from_config(alembic_config()).get_current_head()
        assert version == head
    finally:
        second.close()
