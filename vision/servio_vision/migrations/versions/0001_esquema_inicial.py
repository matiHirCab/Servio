"""Esquema inicial: la base SQLite de la hackathon, tal como la creaba `db.py` con
`CREATE TABLE IF NOT EXISTS` (sin `motivo_cierre` ni `local_id`).

Una base que ya existía con estas tablas se marca (`stamp`) en esta revisión o en
la 0002 según tenga o no `motivo_cierre`, y de ahí se actualiza.

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mozos",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("nombre", sa.String(), nullable=False),
    )
    op.create_table(
        "mesas",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("nombre", sa.String(), nullable=False),
        sa.Column("mozo_id", sa.String(), sa.ForeignKey("mozos.id")),
    )
    op.create_table(
        "platos",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("nombre", sa.String(), nullable=False, unique=True),
    )
    op.create_table(
        "respuestas_tipicas",
        sa.Column(
            "plato_id", sa.String(), sa.ForeignKey("platos.id"), primary_key=True
        ),
        sa.Column("orden", sa.Integer(), primary_key=True),
        sa.Column("texto", sa.String(), nullable=False),
    )
    op.create_table(
        "platos_servidos",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("mesa_id", sa.String(), sa.ForeignKey("mesas.id"), nullable=False),
        sa.Column("plato_id", sa.String(), sa.ForeignKey("platos.id")),
        sa.Column("track_id", sa.String()),
        sa.Column("servido_at", sa.String(), nullable=False),
        sa.Column("origen", sa.String(), nullable=False),
    )
    op.create_table(
        "avisos",
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
    )
    op.create_table(
        "respuestas",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("aviso_id", sa.String(), sa.ForeignKey("avisos.id"), nullable=False),
        sa.Column("mozo_id", sa.String(), sa.ForeignKey("mozos.id")),
        sa.Column("respondido_por", sa.String()),
        sa.Column("texto", sa.String(), nullable=False),
        sa.Column("escrita", sa.Integer(), nullable=False),
        sa.Column("segundos", sa.Float()),
        sa.Column("respondido_at", sa.String(), nullable=False),
    )


def downgrade() -> None:
    for table in (
        "respuestas",
        "avisos",
        "platos_servidos",
        "respuestas_tipicas",
        "platos",
        "mesas",
        "mozos",
    ):
        op.drop_table(table)
