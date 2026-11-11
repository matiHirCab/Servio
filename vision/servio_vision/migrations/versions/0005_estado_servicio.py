"""`estado_servicio`: la foto del estado de las mesas y los avisos abiertos, para que
un reinicio de Servio siga donde quedó (docs/roadmap-producto.md, A-5).

Una fila por local: `estado` es un JSON que escribe y lee `alerts/persist.py`, y
`guardado_at` dice cuándo (si es muy viejo, al arrancar se descarta).

Revision ID: 0005
Revises: 0004
"""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "estado_servicio",
        sa.Column("local_id", sa.String(), primary_key=True),
        sa.Column("guardado_at", sa.String(), nullable=False),
        sa.Column("estado", sa.String(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("estado_servicio")
