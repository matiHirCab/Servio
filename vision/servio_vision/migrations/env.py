"""Entorno de Alembic de Servio: corre sobre la conexión que le pasa `db.Database`.

No se usa `alembic.ini`: `Database._upgrade` arma la configuración en código y deja
la conexión en `config.attributes["connection"]`. Para crear una migración a mano:

    cd vision && python -m alembic -c servio_vision/migrations/alembic.ini revision -m "qué cambia"

(`render_as_batch`: en SQLite, cambiar una tabla es copiarla y renombrarla.)
"""

from alembic import context

from servio_vision.db import metadata

connection = context.config.attributes.get("connection")
if connection is None:
    raise RuntimeError("Las migraciones se corren desde servio_vision.db.Database")

context.configure(connection=connection, target_metadata=metadata, render_as_batch=True)
with context.begin_transaction():
    context.run_migrations()
