"""`respuestas.accion`: qué contestó el mozo (C-5). Con los botones de todos los
avisos, una fila de `respuestas` ya no es siempre un motivo del plato: puede ser
«Voy» (`voy`), «Listo» (`listo`), «No hacía falta» (`no_hacia_falta`) o el motivo
de un plato lleno (`motivo`). Las métricas de platos con problemas miran solo los
motivos; el tiempo de respuesta, la primera respuesta de cualquier tipo.

Lo que había antes eran todos motivos (A-4 solo preguntaba «¿qué pasó?»).

Revision ID: 0007
Revises: 0006
"""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "accion" not in {c["name"] for c in inspector.get_columns("respuestas")}:
        op.add_column("respuestas", sa.Column("accion", sa.String()))
    op.execute("UPDATE respuestas SET accion = 'motivo' WHERE accion IS NULL")


def downgrade() -> None:
    with op.batch_alter_table("respuestas") as batch:
        batch.drop_column("accion")
