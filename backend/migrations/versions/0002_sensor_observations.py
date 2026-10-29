"""Persist edge sensor observations and their analysis response."""
from alembic import op
import sqlalchemy as sa

revision = "0002_sensor_observations"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "sensor_observations" not in inspector.get_table_names():
        op.create_table(
            "sensor_observations",
            sa.Column("observation_id", sa.String(64), primary_key=True),
            sa.Column("sensor_id", sa.String(128), nullable=False),
            sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
            sa.Column("simulation", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("payload", sa.JSON(), nullable=False),
            sa.Column("result", sa.JSON(), nullable=False),
            sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False),
        )
    existing_indexes = {item["name"] for item in sa.inspect(bind).get_indexes("sensor_observations")}
    for name, columns in (
        ("ix_sensor_observations_sensor_id", ["sensor_id"]),
        ("ix_sensor_observations_timestamp", ["timestamp"]),
        ("ix_sensor_observations_simulation", ["simulation"]),
        ("ix_sensor_observations_sensor_timestamp", ["sensor_id", "timestamp"]),
    ):
        if name not in existing_indexes:
            op.create_index(name, "sensor_observations", columns)


def downgrade():
    op.drop_index("ix_sensor_observations_sensor_timestamp", table_name="sensor_observations")
    op.drop_index("ix_sensor_observations_simulation", table_name="sensor_observations")
    op.drop_index("ix_sensor_observations_timestamp", table_name="sensor_observations")
    op.drop_index("ix_sensor_observations_sensor_id", table_name="sensor_observations")
    op.drop_table("sensor_observations")
