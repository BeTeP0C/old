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


class AiEntity(BaseModel):
    """Сущность (сотрудник/команда), упомянутая в ответе — для кликабельных
    переходов из чата. Заполняется детерминированно на бэке, не моделью."""

    type: Literal["employee", "team"]
    id: UUID
    label: str
    subtitle: str | None = None


class AiAction(BaseModel):
    """Предлагаемое действие-переход из чата (подбор встречи, рекомендации)."""

    type: Literal["open_team_meeting", "open_recommendations"]
    label: str
    team_id: UUID | None = None


class AiChatResponse(BaseModel):
    summary: str
    answer: str
    # Поля-списки имеют default=[], чтобы на болтовню («привет») модель могла
    # законно вернуть пустые reasons/recommended_actions, а не выдумывать их.
    reasons: list[AiReason] = Field(default_factory=list)
    recommended_actions: list[AiRecommendedAction] = Field(default_factory=list)
    missing_data: list[str] = Field(default_factory=list)
    used_context: list[str] = Field(default_factory=list)
    # Детерминированно дополняются на бэке (см. AIService._enrich_response).
    entities: list[AiEntity] = Field(default_factory=list)
    actions: list[AiAction] = Field(default_factory=list)


class EmployeeAiExplanationRequest(BaseModel):
    use_rag: bool = True


class EmployeeAiExplanationResponse(BaseModel):
    summary: str
    risk_level: str | None = None
    reasons: list[AiReason] = Field(default_factory=list)
    recommended_actions: list[AiRecommendedAction] = Field(default_factory=list)
    missing_data: list[str] = Field(default_factory=list)
    used_context: list[str] = Field(default_factory=list)
    entities: list[AiEntity] = Field(default_factory=list)
    actions: list[AiAction] = Field(default_factory=list)


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
