from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class ImportLogResponse(BaseModel):
    id: UUID
    source: str
    file_name: str | None
    status: str
    imported_count: int
    skipped_duplicate_count: int
    error_count: int
    created_by: UUID | None
    created_at: datetime
    has_file: bool

    model_config = ConfigDict(from_attributes=True)
