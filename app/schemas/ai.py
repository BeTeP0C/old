from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class AiChatRequest(BaseModel):
    question: str = Field(min_length=1)
    employee_id: UUID | None = None
    team_id: UUID | None = None
    use_rag: bool = True


class AiReason(BaseModel):
    text: str
    source_type: str | None = None
    source_id: str | None = None


class AiRecommendedAction(BaseModel):
    priority: Literal["low", "medium", "high", "critical"]
    action: str
    reason: str


class AiChatResponse(BaseModel):
    summary: str
    answer: str
    reasons: list[AiReason]
    recommended_actions: list[AiRecommendedAction]
    missing_data: list[str]
    used_context: list[str]


class EmployeeAiExplanationRequest(BaseModel):
    use_rag: bool = True


class EmployeeAiExplanationResponse(BaseModel):
    summary: str
    risk_level: str | None = None
    reasons: list[AiReason]
    recommended_actions: list[AiRecommendedAction]
    missing_data: list[str]
    used_context: list[str]


class DocumentIngestRequest(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    source_type: str = Field(min_length=1, max_length=50)
    source_name: str | None = Field(default=None, max_length=255)
    content: str = Field(min_length=1)


class DocumentIngestResponse(BaseModel):
    document_id: UUID
    chunks_created: int


class AiDocumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str
    source_type: str
    source_name: str | None
    content_hash: str | None


class AiChunkResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    document_id: UUID
    chunk_index: int
    content: str
