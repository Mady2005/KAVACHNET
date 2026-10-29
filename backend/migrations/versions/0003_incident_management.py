"""Persist correlated Edge AI incidents and their event links."""
from alembic import op
import sqlalchemy as sa

revision = "0003_incident_management"
down_revision = "0002_sensor_observations"
branch_labels = None
depends_on = None


def upgrade():
    # Nullable to preserve observations ingested before event linkage existed.
    with op.batch_alter_table("sensor_observations") as batch:
        batch.add_column(sa.Column("event_id", sa.String(36), nullable=True))
        batch.create_foreign_key(
            "fk_sensor_observations_event_id_kavach_events",
            "kavach_events", ["event_id"], ["event_id"],
        )
    op.create_index("uq_sensor_observations_event_id", "sensor_observations", ["event_id"], unique=True)

    op.create_table(
        "incidents",
        sa.Column("incident_id", sa.String(36), primary_key=True),
        sa.Column("title", sa.String(240), nullable=False),
        sa.Column("category", sa.String(100), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="OPEN"),
        sa.Column("source_module", sa.String(20), nullable=False, server_default="EDGE_AI"),
        sa.Column("source_id", sa.String(128), nullable=False),
        sa.Column("first_observed", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_observed", sa.DateTime(timezone=True), nullable=False),
        sa.Column("event_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution_notes", sa.Text(), nullable=True),
        sa.CheckConstraint("severity IN ('INFO','LOW','MEDIUM','HIGH','CRITICAL')", name="ck_incidents_severity"),
        sa.CheckConstraint("status IN ('OPEN','ACKNOWLEDGED','RESOLVED')", name="ck_incidents_status"),
        sa.CheckConstraint("event_count >= 0", name="ck_incidents_event_count"),
    )
    for name, columns in (
        ("ix_incidents_category", ["category"]),
        ("ix_incidents_severity", ["severity"]),
        ("ix_incidents_status", ["status"]),
        ("ix_incidents_source_module", ["source_module"]),
        ("ix_incidents_source_id", ["source_id"]),
        ("ix_incidents_first_observed", ["first_observed"]),
        ("ix_incidents_last_observed", ["last_observed"]),
        ("ix_incidents_source_category_status", ["source_id", "category", "status"]),
    ):
        op.create_index(name, "incidents", columns)

    op.create_table(
        "incident_event_links",
        sa.Column("event_id", sa.String(36), sa.ForeignKey("kavach_events.event_id"), primary_key=True),
        sa.Column("incident_id", sa.String(36), sa.ForeignKey("incidents.incident_id"), nullable=False),
        sa.Column("observation_id", sa.String(64), sa.ForeignKey("sensor_observations.observation_id"), nullable=True),
        sa.Column("linked_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("observation_id", name="uq_incident_event_observation"),
    )
    op.create_index("ix_incident_event_links_incident_id", "incident_event_links", ["incident_id"])

    op.create_table(
        "incident_status_history",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("incident_id", sa.String(36), sa.ForeignKey("incidents.incident_id", ondelete="CASCADE"), nullable=False),
        sa.Column("actor_user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("from_status", sa.String(20), nullable=True),
        sa.Column("to_status", sa.String(20), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_incident_status_history_incident_id", "incident_status_history", ["incident_id"])
    op.create_index("ix_incident_status_history_actor_user_id", "incident_status_history", ["actor_user_id"])
    op.create_index("ix_incident_status_history_changed_at", "incident_status_history", ["changed_at"])

    op.create_table(
        "incident_correlation_locks",
        sa.Column("group_key", sa.String(64), primary_key=True),
        sa.Column("lock_version", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade():
    op.drop_table("incident_correlation_locks")
    op.drop_index("ix_incident_status_history_changed_at", table_name="incident_status_history")
    op.drop_index("ix_incident_status_history_actor_user_id", table_name="incident_status_history")
    op.drop_index("ix_incident_status_history_incident_id", table_name="incident_status_history")
    op.drop_table("incident_status_history")
    op.drop_index("ix_incident_event_links_incident_id", table_name="incident_event_links")
    op.drop_table("incident_event_links")
    for index_name in (
        "ix_incidents_source_category_status", "ix_incidents_last_observed",
        "ix_incidents_first_observed", "ix_incidents_source_id",
        "ix_incidents_source_module", "ix_incidents_status",
        "ix_incidents_severity", "ix_incidents_category",
    ):
        op.drop_index(index_name, table_name="incidents")
    op.drop_table("incidents")
    op.drop_index("uq_sensor_observations_event_id", table_name="sensor_observations")
    with op.batch_alter_table("sensor_observations") as batch:
        batch.drop_constraint("fk_sensor_observations_event_id_kavach_events", type_="foreignkey")
        batch.drop_column("event_id")
