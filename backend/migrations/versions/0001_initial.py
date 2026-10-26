"""Initialize the Phase 1 KAVACHNET shared schema."""
from alembic import op
from app.db.base import Base
import app.models
revision="0001_initial"
down_revision=None
branch_labels=None
depends_on=None
PHASE1_TABLES=("users","audit_logs","kavach_events","decision_records","system_settings","dataset_registry","revoked_tokens")
def upgrade(): Base.metadata.create_all(bind=op.get_bind(),tables=[Base.metadata.tables[name] for name in PHASE1_TABLES])
def downgrade(): Base.metadata.drop_all(bind=op.get_bind(),tables=[Base.metadata.tables[name] for name in PHASE1_TABLES])
