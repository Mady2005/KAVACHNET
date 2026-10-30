"""Persist RIPE Atlas public measurement ingestion runs and observations."""
from alembic import op
import sqlalchemy as sa

revision = "0004_ripe_atlas_public"
down_revision = "0003_network_provenance"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("network_observations") as batch:
        batch.drop_constraint("ck_network_observation_source", type_="check")
        batch.create_check_constraint(
            "ck_network_observation_source",
            "source_type IN ('REAL_MEASUREMENT','PUBLIC_DATASET','SIMULATED','RIPE_ATLAS_PUBLIC')",
        )
    op.create_table(
        "public_measurement_ingestion_runs",
        sa.Column("run_id", sa.String(36), primary_key=True),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("measurement_id", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("requested_by", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("retrieved_count", sa.Integer(), nullable=False),
        sa.Column("accepted_count", sa.Integer(), nullable=False),
        sa.Column("rejected_count", sa.Integer(), nullable=False),
        sa.Column("deduplicated_count", sa.Integer(), nullable=False),
        sa.Column("observations_inserted_count", sa.Integer(), nullable=False),
        sa.Column("observations_deduplicated_count", sa.Integer(), nullable=False),
        sa.Column("errors", sa.JSON(), nullable=False),
    )
    for column in ("measurement_id", "status", "requested_by"):
        op.create_index(f"ix_public_measurement_ingestion_runs_{column}", "public_measurement_ingestion_runs", [column])


def downgrade():
    for column in ("requested_by", "status", "measurement_id"):
        op.drop_index(f"ix_public_measurement_ingestion_runs_{column}", table_name="public_measurement_ingestion_runs")
    op.drop_table("public_measurement_ingestion_runs")
    with op.batch_alter_table("network_observations") as batch:
        batch.drop_constraint("ck_network_observation_source", type_="check")
        batch.create_check_constraint(
            "ck_network_observation_source",
            "source_type IN ('REAL_MEASUREMENT','PUBLIC_DATASET','SIMULATED')",
        )
