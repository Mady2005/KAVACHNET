"""Add source provenance and bounded real measurement runs."""
from alembic import op
import sqlalchemy as sa

revision = "0003_network_provenance"
down_revision = "0002_network_resilience"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("network_telemetry", sa.Column("source_type", sa.String(24), nullable=False, server_default="SIMULATED"))
    op.create_index("ix_network_telemetry_source_type", "network_telemetry", ["source_type"])
    op.create_table(
        "network_observations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("timestamp_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_type", sa.String(24), nullable=False),
        sa.Column("source_name", sa.String(200), nullable=False),
        sa.Column("measurement_type", sa.String(80), nullable=False),
        sa.Column("value", sa.Float(), nullable=True),
        sa.Column("unit", sa.String(32), nullable=False),
        sa.Column("target_label", sa.String(120), nullable=True),
        sa.Column("measurement_method", sa.String(200), nullable=False),
        sa.Column("collection_interval_seconds", sa.Float(), nullable=True),
        sa.Column("dataset_version", sa.String(100), nullable=True),
        sa.Column("experiment_id", sa.String(100), nullable=True),
        sa.Column("dataset_id", sa.String(36), sa.ForeignKey("dataset_registry.dataset_id"), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("source_type IN ('REAL_MEASUREMENT','PUBLIC_DATASET','SIMULATED')", name="ck_network_observation_source"),
        sa.CheckConstraint("value IS NULL OR value >= 0", name="ck_network_observation_value"),
    )
    for column in ("timestamp_utc", "source_type", "source_name", "measurement_type", "target_label", "experiment_id", "dataset_id", "created_at"):
        op.create_index(f"ix_network_observations_{column}", "network_observations", [column])
    op.create_table(
        "network_measurement_runs",
        sa.Column("run_id", sa.String(36), primary_key=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("target_label", sa.String(120), nullable=False),
        sa.Column("requested_by", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("requested_samples", sa.Integer(), nullable=False),
        sa.Column("interval_seconds", sa.Float(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("successful_probes", sa.Integer(), nullable=False),
        sa.Column("failed_probes", sa.Integer(), nullable=False),
        sa.Column("error_categories", sa.JSON(), nullable=False),
    )
    op.create_index("ix_network_measurement_runs_status", "network_measurement_runs", ["status"])
    op.create_index("ix_network_measurement_runs_requested_by", "network_measurement_runs", ["requested_by"])


def downgrade():
    op.drop_index("ix_network_measurement_runs_requested_by", table_name="network_measurement_runs")
    op.drop_index("ix_network_measurement_runs_status", table_name="network_measurement_runs")
    op.drop_table("network_measurement_runs")
    for column in ("created_at", "dataset_id", "experiment_id", "target_label", "measurement_type", "source_name", "source_type", "timestamp_utc"):
        op.drop_index(f"ix_network_observations_{column}", table_name="network_observations")
    op.drop_table("network_observations")
    op.drop_index("ix_network_telemetry_source_type", table_name="network_telemetry")
    op.drop_column("network_telemetry", "source_type")
