"""Initialize the complete KAVACHNET shared schema."""
from alembic import op
from app.db.base import Base
import app.models
revision="0001_initial"
down_revision=None
branch_labels=None
depends_on=None
def upgrade():
    # Keep this baseline revision stable as later models are added to metadata.
    later_member2_tables = {
        "sensor_observations", "incidents", "incident_event_links",
        "incident_status_history", "incident_correlation_locks",
    }
    tables = [table for table in Base.metadata.sorted_tables if table.name not in later_member2_tables]
    Base.metadata.create_all(bind=op.get_bind(), tables=tables)

def downgrade(): Base.metadata.drop_all(bind=op.get_bind())
