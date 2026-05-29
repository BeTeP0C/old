from typing import Literal
from uuid import UUID

from pydantic import BaseModel

RECOMMENDATION_TARGET_STATUSES = ("requested", "deferred", "ignored", "pending")

RecommendationTargetStatus = Literal["requested", "deferred", "ignored", "pending"]
RecommendationSubjectType = Literal["employee", "team"]
RecommendationSeverity = Literal["critical", "high", "medium"]


class RecommendationResponse(BaseModel):
    code: str
    reason: str
    severity: str
    action: str
    subject_type: str
    subject_id: UUID
    status: str | None = None
    roadmap_item_id: UUID | None = None


class RecommendationStatusUpdateRequest(BaseModel):
    status: RecommendationTargetStatus


class RecommendationBulkStatusRequest(BaseModel):
    status: RecommendationTargetStatus
    severity: RecommendationSeverity | None = None
    subject_type: RecommendationSubjectType | None = None


class RecommendationBulkStatusResponse(BaseModel):
    updated: int
    skipped: int
