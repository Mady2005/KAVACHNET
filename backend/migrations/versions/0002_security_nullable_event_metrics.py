"""Allow Security events to mark unrelated shared metrics as not applicable."""

from alembic import op
import sqlalchemy as sa


revision = "0002_security_nullable"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Batch mode preserves existing rows on SQLite and emits ALTER COLUMN on
    # databases that support it natively.
    with op.batch_alter_table("kavach_events") as batch_op:
        batch_op.alter_column(
            "situation_risk",
            existing_type=sa.Float(),
            existing_nullable=False,
            nullable=True,
        )
        batch_op.alter_column(
            "network_health",
            existing_type=sa.Float(),
            existing_nullable=False,
            nullable=True,
        )


def downgrade() -> None:
    connection = op.get_bind()
    null_rows = connection.execute(
        sa.text(
            "SELECT COUNT(*) FROM kavach_events "
            "WHERE situation_risk IS NULL OR network_health IS NULL"
        )
    ).scalar_one()
    if null_rows:
        raise RuntimeError(
            "Cannot downgrade while event rows have NULL situation_risk or network_health"
        )

    with op.batch_alter_table("kavach_events") as batch_op:
        batch_op.alter_column(
            "situation_risk",
            existing_type=sa.Float(),
            existing_nullable=True,
            nullable=False,
        )
        batch_op.alter_column(
            "network_health",
            existing_type=sa.Float(),
            existing_nullable=True,
            nullable=False,
        )
