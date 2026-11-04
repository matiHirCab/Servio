"""Datos para las métricas de M-1 (docs/context/13-metricas.md).

- `platos.precio`: el precio de la carta, para el desperdicio en $.
- `platos_servidos.ultimo_fill_ratio` y `retirado_at`: cuánto le quedaba al plato
  en la última lectura y cuándo se fue de la mesa (sobra por plato).
- `estados_plato`: cada cambio de estado de un plato servido (ritmo por plato).

Una base que ya tenga alguna de estas columnas (la rama de M-1 las agregaba antes
de Alembic) no las vuelve a crear.

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

COLUMNS = (
    ("platos", "precio", sa.Float()),
    ("platos_servidos", "ultimo_fill_ratio", sa.Float()),
    ("platos_servidos", "retirado_at", sa.String()),
)


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    for table, column, kind in COLUMNS:
        if column not in {c["name"] for c in inspector.get_columns(table)}:
            op.add_column(table, sa.Column(column, kind))
    if "estados_plato" not in inspector.get_table_names():
        op.create_table(
            "estados_plato",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column(
                "plato_servido_id",
                sa.Integer(),
                sa.ForeignKey("platos_servidos.id"),
                nullable=False,
            ),
            sa.Column("estado", sa.String(), nullable=False),
            sa.Column("fill_ratio", sa.Float()),
            sa.Column("desde", sa.String(), nullable=False),
        )


def downgrade() -> None:
    op.drop_table("estados_plato")
    with op.batch_alter_table("platos_servidos") as batch:
        batch.drop_column("retirado_at")
        batch.drop_column("ultimo_fill_ratio")
    with op.batch_alter_table("platos") as batch:
        batch.drop_column("precio")
