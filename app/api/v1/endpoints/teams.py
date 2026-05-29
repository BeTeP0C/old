from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db_session, require_roles
from app.core.roles import EmployeeRole
from app.models.employee import Employee
from app.schemas.availability import (
    MeetingRecommendationRequest,
    MeetingRecommendationResponse,
    TeamAvailabilityResponse,
)
from app.schemas.common import ErrorResponse
from app.schemas.team import (
    TeamAvailabilityRankingItem,
    TeamCreate,
    TeamMetricsResponse,
    TeamResponse,
    TeamUpdate,
)
from app.schemas.team_member import TeamMemberCreate, TeamMemberResponse
from app.services.exceptions import InvalidOperationError, NotFoundError
from app.services.team_availability import TeamAvailabilityService
from app.services.team_members import TeamMemberService
from app.services.team_metrics import TeamMetricsService
from app.services.teams import TeamService

router = APIRouter(prefix="/teams", tags=["teams"])
SessionDep = Annotated[AsyncSession, Depends(get_db_session)]

error_responses: dict[int | str, dict[str, Any]] = {
    status.HTTP_400_BAD_REQUEST: {"model": ErrorResponse},
    status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
}


@router.post(
    "",
    response_model=TeamResponse,
    status_code=status.HTTP_201_CREATED,
    responses=error_responses,
)
async def create_team(
    payload: TeamCreate,
    session: SessionDep,
    _current_employee: Annotated[
        Employee,
        Depends(
            require_roles(EmployeeRole.ADMIN, EmployeeRole.MANAGER, EmployeeRole.PM)
        ),
    ],
) -> TeamResponse:
    try:
        team = await TeamService(session).create(payload)
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvalidOperationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    response = TeamResponse.model_validate(team)
    response.members_count = len(payload.members)
    return response


@router.get("", response_model=list[TeamResponse])
async def list_teams(
    session: SessionDep,
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[TeamResponse]:
    teams_with_counts = await TeamService(session).list_with_counts(skip=skip, limit=limit)
    items: list[TeamResponse] = []
    for team, count in teams_with_counts:
        # Конструируем response явно, чтобы pydantic не пытался лениво загрузить
        # relationship `team.members` в async-контексте (MissingGreenlet).
        # В листинге `members` остаётся None — детальный состав отдаём только
        # из /teams/{id}.
        items.append(
            TeamResponse(
                id=team.id,
                name=team.name,
                description=team.description,
                avatar_url=team.avatar_url,
                members_count=count,
                members=None,
                created_at=team.created_at,
                updated_at=team.updated_at,
            )
        )
    return items


@router.get(
    "/availability-ranking",
    response_model=list[TeamAvailabilityRankingItem],
)
async def list_team_availability_ranking(
    session: SessionDep,
    window_days: int = 7,
) -> list[TeamAvailabilityRankingItem]:
    """Рейтинг команд по доступному окну пересечения (ТЗ §8).

    Команды с самым низким `overlap_ratio` идут первыми — это те, кому нужны
    решения о времени совместных встреч.
    """
    return await TeamMetricsService(session).availability_ranking(window_days=window_days)


@router.get(
    "/{team_id}/metrics",
    response_model=TeamMetricsResponse,
    responses=error_responses,
)
async def get_team_metrics(
    team_id: UUID,
    session: SessionDep,
) -> TeamMetricsResponse:
    try:
        return await TeamMetricsService(session).get(team_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get("/{team_id}", response_model=TeamResponse, responses=error_responses)
async def get_team(
    team_id: UUID,
    session: SessionDep,
) -> TeamResponse:
    service = TeamService(session)
    try:
        team = await service.get(team_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    members = await service.team_members.list_for_team(team_id)
    # Конструируем response явно, чтобы не триггерить lazy-load relationship
    # `team.members` (он вызвал бы MissingGreenlet в async-контексте).
    return TeamResponse(
        id=team.id,
        name=team.name,
        description=team.description,
        avatar_url=team.avatar_url,
        members_count=len(members),
        members=[TeamMemberResponse.model_validate(m) for m in members],
        created_at=team.created_at,
        updated_at=team.updated_at,
    )


@router.patch("/{team_id}", response_model=TeamResponse, responses=error_responses)
async def update_team(
    team_id: UUID,
    payload: TeamUpdate,
    session: SessionDep,
    _current_employee: Annotated[
        Employee,
        Depends(
            require_roles(EmployeeRole.ADMIN, EmployeeRole.MANAGER, EmployeeRole.PM)
        ),
    ],
) -> TeamResponse:
    try:
        team = await TeamService(session).update(team_id, payload)
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return TeamResponse.model_validate(team)


@router.delete(
    "/{team_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=error_responses,
)
async def delete_team(
    team_id: UUID,
    session: SessionDep,
    _current_employee: Annotated[
        Employee,
        Depends(
            require_roles(EmployeeRole.ADMIN, EmployeeRole.MANAGER, EmployeeRole.PM)
        ),
    ],
) -> Response:
    try:
        await TeamService(session).delete(team_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{team_id}/members",
    response_model=TeamMemberResponse,
    status_code=status.HTTP_201_CREATED,
    responses=error_responses,
)
async def create_team_member(
    team_id: UUID,
    payload: TeamMemberCreate,
    session: SessionDep,
    _current_employee: Annotated[
        Employee,
        Depends(
            require_roles(
                EmployeeRole.ADMIN,
                EmployeeRole.MANAGER,
                EmployeeRole.HR,
                EmployeeRole.PM,
            )
        ),
    ],
) -> TeamMemberResponse:
    try:
        team_member = await TeamMemberService(session).create(team_id, payload)
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvalidOperationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return TeamMemberResponse.model_validate(team_member)


@router.delete(
    "/{team_id}/members/{employee_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=error_responses,
)
async def delete_team_member(
    team_id: UUID,
    employee_id: UUID,
    session: SessionDep,
    _current_employee: Annotated[
        Employee,
        Depends(
            require_roles(
                EmployeeRole.ADMIN,
                EmployeeRole.MANAGER,
                EmployeeRole.HR,
                EmployeeRole.PM,
            )
        ),
    ],
) -> Response:
    try:
        await TeamMemberService(session).delete(team_id, employee_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/{team_id}/availability",
    response_model=TeamAvailabilityResponse,
    responses=error_responses,
)
async def get_team_availability(
    team_id: UUID,
    start_dt: datetime,
    end_dt: datetime,
    session: SessionDep,
) -> TeamAvailabilityResponse:
    try:
        return await TeamAvailabilityService(session).get_availability(team_id, start_dt, end_dt)
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvalidOperationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post(
    "/{team_id}/meeting-recommendations",
    response_model=list[MeetingRecommendationResponse],
    responses=error_responses,
)
async def get_team_meeting_recommendations(
    team_id: UUID,
    payload: MeetingRecommendationRequest,
    session: SessionDep,
    _current_employee: Annotated[
        Employee,
        Depends(
            require_roles(EmployeeRole.ADMIN, EmployeeRole.MANAGER, EmployeeRole.PM)
        ),
    ],
) -> list[MeetingRecommendationResponse]:
    try:
        return await TeamAvailabilityService(session).recommend_meetings(team_id, payload)
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvalidOperationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
