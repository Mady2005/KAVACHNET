"""Add persisted network telemetry, messages, and delivery attempts."""
from alembic import op
import sqlalchemy as sa

revision="0002_network_resilience"
down_revision="0001_initial"
branch_labels=None
depends_on=None

def upgrade():
    op.create_table(
        "network_telemetry",
        sa.Column("id",sa.Integer(),primary_key=True),
        sa.Column("scenario",sa.String(length=20),nullable=False),
        sa.Column("state",sa.String(length=20),nullable=False),
        sa.Column("timestamp",sa.DateTime(timezone=True),nullable=False),
        sa.Column("latency_ms",sa.Float(),nullable=False),
        sa.Column("packet_loss_pct",sa.Float(),nullable=False),
        sa.Column("bandwidth_mbps",sa.Float(),nullable=False),
        sa.Column("connected",sa.Boolean(),nullable=False),
        sa.Column("jitter_ms",sa.Float(),nullable=True),
        sa.Column("delivery_failure_rate_pct",sa.Float(),nullable=False),
        sa.Column("health_score",sa.Float(),nullable=False),
        sa.Column("reason",sa.String(length=500),nullable=False),
        sa.CheckConstraint("latency_ms >= 0",name="ck_network_telemetry_latency"),
        sa.CheckConstraint("packet_loss_pct >= 0 AND packet_loss_pct <= 100",name="ck_network_telemetry_loss"),
        sa.CheckConstraint("bandwidth_mbps >= 0",name="ck_network_telemetry_bandwidth"),
        sa.CheckConstraint("delivery_failure_rate_pct >= 0 AND delivery_failure_rate_pct <= 100",name="ck_network_telemetry_failure_rate"),
        sa.CheckConstraint("health_score >= 0 AND health_score <= 100",name="ck_network_telemetry_health"),
    )
    op.create_index("ix_network_telemetry_scenario","network_telemetry",["scenario"])
    op.create_index("ix_network_telemetry_state","network_telemetry",["state"])
    op.create_index("ix_network_telemetry_timestamp","network_telemetry",["timestamp"])
    op.create_table(
        "network_messages",
        sa.Column("message_id",sa.String(length=36),primary_key=True),
        sa.Column("idempotency_key",sa.String(length=100),nullable=True,unique=True),
        sa.Column("payload_sha256",sa.String(length=64),nullable=False),
        sa.Column("payload_blob",sa.LargeBinary(),nullable=False),
        sa.Column("payload_encoding",sa.String(length=20),nullable=False),
        sa.Column("compression_algorithm",sa.String(length=20),nullable=True),
        sa.Column("original_size",sa.Integer(),nullable=False),
        sa.Column("stored_size",sa.Integer(),nullable=False),
        sa.Column("priority",sa.String(length=2),nullable=False),
        sa.Column("state",sa.String(length=20),nullable=False),
        sa.Column("attempts",sa.Integer(),nullable=False),
        sa.Column("created_at",sa.DateTime(timezone=True),nullable=False),
        sa.Column("updated_at",sa.DateTime(timezone=True),nullable=False),
        sa.Column("next_attempt_at",sa.DateTime(timezone=True),nullable=True),
        sa.Column("last_attempt_at",sa.DateTime(timezone=True),nullable=True),
        sa.Column("delivered_at",sa.DateTime(timezone=True),nullable=True),
        sa.Column("expires_at",sa.DateTime(timezone=True),nullable=False),
        sa.Column("last_error_category",sa.String(length=80),nullable=True),
        sa.CheckConstraint("original_size >= 0 AND stored_size >= 0",name="ck_network_message_sizes"),
        sa.CheckConstraint("attempts >= 0",name="ck_network_message_attempts"),
    )
    for col in ("priority","state","created_at","next_attempt_at","expires_at"):
        op.create_index(f"ix_network_messages_{col}","network_messages",[col])
    op.create_table(
        "network_delivery_attempts",
        sa.Column("id",sa.Integer(),primary_key=True),
        sa.Column("message_id",sa.String(length=36),sa.ForeignKey("network_messages.message_id",ondelete="CASCADE"),nullable=False),
        sa.Column("attempt_number",sa.Integer(),nullable=False),
        sa.Column("attempted_at",sa.DateTime(timezone=True),nullable=False),
        sa.Column("outcome",sa.String(length=20),nullable=False),
        sa.Column("error_category",sa.String(length=80),nullable=True),
        sa.Column("latency_ms",sa.Float(),nullable=True),
        sa.UniqueConstraint("message_id","attempt_number",name="uq_network_attempt_number"),
    )
    op.create_index("ix_network_delivery_attempts_message_id","network_delivery_attempts",["message_id"])
    op.create_index("ix_network_delivery_attempts_attempted_at","network_delivery_attempts",["attempted_at"])

def downgrade():
    op.drop_index("ix_network_delivery_attempts_attempted_at",table_name="network_delivery_attempts")
    op.drop_index("ix_network_delivery_attempts_message_id",table_name="network_delivery_attempts")
    op.drop_table("network_delivery_attempts")
    for col in ("expires_at","next_attempt_at","created_at","state","priority"):
        op.drop_index(f"ix_network_messages_{col}",table_name="network_messages")
    op.drop_table("network_messages")
    for col in ("timestamp","state","scenario"):
        op.drop_index(f"ix_network_telemetry_{col}",table_name="network_telemetry")
    op.drop_table("network_telemetry")
