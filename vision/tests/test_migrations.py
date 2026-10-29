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


def test_upgrade_head_creates_every_model_table(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'a.db'}")
    run(engine, command.upgrade, "head")
    assert set(metadata.tables) <= tables(engine)


def test_last_migration_downgrades_and_upgrades_again(tmp_path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'b.db'}")
    run(engine, command.upgrade, "head")
    full = tables(engine)

    run(engine, command.downgrade, "0003")
    assert "estados_plato" not in tables(engine)

    run(engine, command.upgrade, "head")
    assert tables(engine) == full


def test_local_id_migration_refuses_to_go_back(tmp_path):
    """0003 no se deshace a propósito: habría que elegir qué local conservar."""
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'c.db'}")
    run(engine, command.upgrade, "0003")
    with pytest.raises(NotImplementedError, match="local_id"):
        run(engine, command.downgrade, "0002")


def test_database_reopened_stays_at_head_and_keeps_rows(tmp_path):
    path = tmp_path / "servio.db"
    first = Database(path)
    first.close()
    second = Database(path)
    try:
        [(version,)] = second.query("SELECT version_num FROM alembic_version")
        head = ScriptDirectory.from_config(alembic_config()).get_current_head()
        assert version == head
    finally:
        second.close()
