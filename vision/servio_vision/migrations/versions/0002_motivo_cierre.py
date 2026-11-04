"""`avisos.motivo_cierre`: por qué se cerró un aviso de mesa terminada ("retirados"
o "servido"). En el historial simulado, toda mesa terminada se cerró retirando los
platos. Es la migración que `db.py` hacía a mano con `PRAGMA table_info`.

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("avisos", sa.Column("motivo_cierre", sa.String()))
    op.execute(
        "UPDATE avisos SET motivo_cierre = 'retirados'"
        " WHERE origen = 'historial' AND tipo = 'mesa_terminada'"
    )


def downgrade() -> None:
    with op.batch_alter_table("avisos") as batch:
        batch.drop_column("motivo_cierre")
