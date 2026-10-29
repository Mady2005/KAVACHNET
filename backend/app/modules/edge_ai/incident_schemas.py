from datetime import datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

IncidentStatus = Literal["OPEN", "ACKNOWLEDGED", "RESOLVED"]
IncidentSeverity = Literal["INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL"]

class IncidentStatusUpdate(BaseModel):
    status: Literal["ACKNOWLEDGED", "RESOLVED"]
    resolution_notes: str | None = Field(default=None, max_length=2000)

class IncidentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    incident_id: str
    title: str
    category: str
    severity: str
    status: str
    source_module: str
    source_id: str
    first_observed: datetime
    last_observed: datetime
    event_count: int
    created_at: datetime
    updated_at: datetime
    resolved_at: datetime | None
    resolution_notes: str | None
