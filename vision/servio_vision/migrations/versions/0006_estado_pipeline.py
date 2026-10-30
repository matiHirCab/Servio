"""`estado_pipeline`: la foto de lo que recuerda el pipeline (ids de los platos,
suavizado, numeración de las mesas, platos del pedido), para que un reinicio de
Servio no los corra. Hermana de `estado_servicio` (la de los avisos): una fila por
local, con un JSON que escriben y leen `Pipeline` y sus piezas.

Revision ID: 0006
Revises: 0005
"""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "estado_pipeline",
        sa.Column("local_id", sa.String(), primary_key=True),
        sa.Column("guardado_at", sa.String(), nullable=False),
        sa.Column("estado", sa.String(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("estado_pipeline")
