"""`local_id`: cada casa (cada local) escribe con el suyo en la misma base (A-6).

- `platos_servidos` y `respuestas` suman la columna.
- `avisos` pasa a clave `(local_id, id)`: el id de un aviso es
  `{mesa}-{tipo}-{segundo}`, único solo dentro de un local; dos casas con una
  "Mesa 1" no se pueden pisar.
- `respuestas.aviso_id` referencia a `avisos(local_id, id)`.

Cambiar la clave de una tabla en SQLite es crearla de nuevo: se crean `avisos` y
`respuestas` con la forma nueva, se copian las filas (todas al local "local") y se
renombran. Lo mismo vale en Postgres. `Database._upgrade` corre esto con las FK de
SQLite apagadas, así borrar las tablas viejas no las frena.

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa
from alembic import op

from servio_vision import DEFAULT_LOCAL as LOCAL

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "platos_servidos",
        sa.Column("local_id", sa.String(), nullable=False, server_default=LOCAL),
    )
    op.create_table(
        "avisos_v3",
        sa.Column("local_id", sa.String(), primary_key=True, server_default=LOCAL),
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("tipo", sa.String(), nullable=False),
        sa.Column("mesa_id", sa.String(), sa.ForeignKey("mesas.id"), nullable=False),
        sa.Column("mozo_id", sa.String(), sa.ForeignKey("mozos.id")),
        sa.Column(
            "plato_servido_id", sa.Integer(), sa.ForeignKey("platos_servidos.id")
        ),
        sa.Column("abierto_at", sa.String(), nullable=False),
        sa.Column("resuelto_at", sa.String()),
        sa.Column("origen", sa.String(), nullable=False),
        sa.Column("motivo_cierre", sa.String()),
    )
    op.execute(
        "INSERT INTO avisos_v3 (local_id, id, tipo, mesa_id, mozo_id, plato_servido_id,"
        " abierto_at, resuelto_at, origen, motivo_cierre)"
        f" SELECT '{LOCAL}', id, tipo, mesa_id, mozo_id, plato_servido_id,"
        " abierto_at, resuelto_at, origen, motivo_cierre FROM avisos"
    )
    op.create_table(
        "respuestas_v3",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("local_id", sa.String(), nullable=False, server_default=LOCAL),
        sa.Column("aviso_id", sa.String(), nullable=False),
        sa.Column("mozo_id", sa.String(), sa.ForeignKey("mozos.id")),
        sa.Column("respondido_por", sa.String()),
        sa.Column("texto", sa.String(), nullable=False),
        sa.Column("escrita", sa.Integer(), nullable=False),
        sa.Column("segundos", sa.Float()),
        sa.Column("respondido_at", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(
            ["local_id", "aviso_id"], ["avisos_v3.local_id", "avisos_v3.id"]
        ),
    )
    op.execute(
        "INSERT INTO respuestas_v3 (id, local_id, aviso_id, mozo_id, respondido_por,"
        " texto, escrita, segundos, respondido_at)"
        f" SELECT id, '{LOCAL}', aviso_id, mozo_id, respondido_por, texto, escrita,"
        " segundos, respondido_at FROM respuestas"
    )
    op.drop_table("respuestas")
    op.drop_table("avisos")
    op.rename_table("avisos_v3", "avisos")
    op.rename_table("respuestas_v3", "respuestas")
    if op.get_bind().dialect.name == "postgresql":
        # las filas copiadas traen su id: la secuencia tiene que seguir después
        op.execute(
            "SELECT setval(pg_get_serial_sequence('respuestas', 'id'),"
            " COALESCE((SELECT MAX(id) FROM respuestas), 1))"
        )


def downgrade() -> None:
    raise NotImplementedError(
        "No se vuelve atrás de local_id: habría que elegir qué local conservar"
    )
