from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.team_member import TeamMemberResponse

TeamRole = Literal["lead", "pm", "analyst", "member"]


class TeamMemberInput(BaseModel):
    """Участник внутри payload'а создания команды."""

    employee_id: UUID
    role_in_team: TeamRole = "member"


class TeamCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=150)
    description: str | None = None
    avatar_url: str | None = Field(default=None, max_length=512)
    members: list[TeamMemberInput] = Field(default_factory=list)


class TeamUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=150)
    description: str | None = None
    avatar_url: str | None = Field(default=None, max_length=512)


class TeamResponse(BaseModel):
    id: UUID
    name: str
    description: str | None
    avatar_url: str | None
    members_count: int | None = None
    # Подробный список участников возвращается только из /teams/{id} (detail-эндпоинт),
    # в листингах остаётся None, чтобы не делать N+1 запросов.
    members: list[TeamMemberResponse] | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TeamMetricsResponse(BaseModel):
    """Агрегат метрик участников команды для рейтинга и фильтров на /teams."""

    team_id: UUID
    members_count: int
    attention_count: int
    outdated_count: int
    avg_actuality: float | None
    avg_load: float | None
    max_risk_level: Literal["low", "medium", "high", "critical"] | None


class TeamAvailabilityRankingItem(BaseModel):
    """Запись рейтинга команд по пересечению доступности (ТЗ §8)."""

    team_id: UUID
    name: str
    members_count: int
    overlap_ratio: float
    full_team_minutes: float
    majority_minutes: float
    total_window_minutes: float
