from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    CurrentEmployeeDep,
    get_db_session,
    require_roles,
    require_roles_or_self_employee,
)
from app.core.roles import MANAGEMENT_ROLES, EmployeeRole
from app.models.employee import Employee
from app.repositories.change_history import ChangeHistoryRepository
from app.schemas.change_history import ChangeHistoryResponse
from app.schemas.common import ErrorResponse
from app.schemas.employee import (
    EmployeeCreate,
    EmployeeFullCreate,
    EmployeeResponse,
    EmployeeUpdate,
)
from app.schemas.schedule_confirmation import (
    BulkScheduleConfirmationRequestCreate,
    BulkScheduleConfirmationRequestResponse,
    ScheduleConfirmationRequestCreate,
    ScheduleConfirmationRequestResponse,
    ScheduleConfirmDeclineRequest,
    ScheduleConfirmResponse,
)
from app.schemas.schedule_diagnostics import ScheduleDiagnosticsResponse
from app.schemas.schedule_exception import (
    ScheduleExceptionCreate,
    ScheduleExceptionResponse,
    ScheduleExceptionUpdate,
)
from app.schemas.work_schedule import WorkScheduleCreate, WorkScheduleResponse
from app.services.audit import ENTITY_SCHEDULE_EXCEPTION, ENTITY_WORK_SCHEDULE
from app.services.employees import EmployeeService
from app.services.exceptions import InvalidOperationError, NotFoundError
from app.services.schedule_confirmations import (
    ScheduleConfirmationService,
)
from app.services.schedule_confirmations import (
    to_response as schedule_confirmation_to_response,
)
from app.services.schedule_diagnostics import ScheduleDiagnosticsService
from app.services.schedule_exceptions import ScheduleExceptionService
from app.services.work_schedules import WorkScheduleService

router = APIRouter(prefix="/employees", tags=["employees"])
SessionDep = Annotated[AsyncSession, Depends(get_db_session)]

error_responses: dict[int | str, dict[str, Any]] = {
    status.HTTP_400_BAD_REQUEST: {"model": ErrorResponse},
    status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
}


@router.post(
    "",
    response_model=EmployeeResponse,
    status_code=status.HTTP_201_CREATED,
    responses=error_responses,
)
async def create_employee(
    payload: EmployeeCreate,
    session: SessionDep,
    _current_employee: Annotated[
        Employee,
        Depends(require_roles(EmployeeRole.ADMIN, EmployeeRole.HR)),
    ],
) -> EmployeeResponse:
    try:
        employee = await EmployeeService(session).create(payload)
    except InvalidOperationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return EmployeeResponse.from_employee(employee)


@router.post(
    "/full",
    response_model=EmployeeResponse,
    status_code=status.HTTP_201_CREATED,
    responses=error_responses,
)
async def create_employee_full(
    payload: EmployeeFullCreate,
    session: SessionDep,
    _current_employee: Annotated[
        Employee,
        Depends(require_roles(EmployeeRole.ADMIN, EmployeeRole.HR)),
    ],
) -> EmployeeResponse:
    try:
        employee = await EmployeeService(session).create_full(payload)
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvalidOperationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return EmployeeResponse.from_employee(employee)



EmployeeCategory = Literal[
    "actual",
    "outdated",
    "outside_schedule",
    "overloaded",
    "in_absence",
    "hr_calendar_conflict",
    "timezone_conflict",
    "needs_review",
    "pending_confirmation",
]


@router.get("", response_model=list[EmployeeResponse])
async def list_employees(
    session: SessionDep,
    response: Response,
    team_id: UUID | None = Query(default=None),
    risk_level: Literal["low", "medium", "high", "critical"] | None = Query(default=None),
    work_format: Literal["office", "remote", "hybrid"] | None = Query(default=None),
    search: str | None = Query(default=None, min_length=1, max_length=200),
    category: EmployeeCategory | None = Query(default=None),
    skip: int = Query(default=0, ge=0),
    limit: int | None = Query(default=None, ge=1, le=500),
) -> list[EmployeeResponse]:
    service = EmployeeService(session)
    if limit is not None:
        # Пагинированный режим: один доп. count-запрос, X-Total-Count в headers.
        total = await service.count(
            team_id=team_id,
            risk_level=risk_level,
            work_format=work_format,
            search=search,
            category=category,
        )
        response.headers["X-Total-Count"] = str(total)
    employees = await service.list(
        team_id=team_id,
        risk_level=risk_level,
        work_format=work_format,
        search=search,
        category=category,
        skip=skip,
        limit=limit,
    )
    return [EmployeeResponse.from_employee(employee) for employee in employees]


@router.get("/{employee_id}", response_model=EmployeeResponse, responses=error_responses)
async def get_employee(
    employee_id: UUID,
    session: SessionDep,
) -> EmployeeResponse:
    try:
        employee = await EmployeeService(session).get(employee_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return EmployeeResponse.from_employee(employee)


@router.patch("/{employee_id}", response_model=EmployeeResponse, responses=error_responses)
async def update_employee(
    employee_id: UUID,
    payload: EmployeeUpdate,
    session: SessionDep,
    current_employee: Annotated[
        Employee,
        Depends(require_roles_or_self_employee(EmployeeRole.ADMIN, EmployeeRole.HR)),
    ],
) -> EmployeeResponse:
    if payload.role is not None and current_employee.role != EmployeeRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="only admin can change role",
        )
    try:
        employee = await EmployeeService(session).update(
            employee_id, payload, changed_by=current_employee.id
        )
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvalidOperationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return EmployeeResponse.from_employee(employee)


@router.post(
    "/{employee_id}/schedules",
    response_model=WorkScheduleResponse,
    status_code=status.HTTP_201_CREATED,
    responses=error_responses,
)
async def create_employee_schedule(
    employee_id: UUID,
    payload: WorkScheduleCreate,
    session: SessionDep,
    current_employee: Annotated[
        Employee,
        Depends(
            require_roles_or_self_employee(
                EmployeeRole.ADMIN, EmployeeRole.HR, EmployeeRole.MANAGER
            )
        ),
    ],
) -> WorkScheduleResponse:
    try:
        schedule = await WorkScheduleService(session).create(
            employee_id, payload, changed_by=current_employee.id
        )
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvalidOperationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return WorkScheduleResponse.model_validate(schedule)


@router.get(
    "/{employee_id}/schedules/active",
    response_model=WorkScheduleResponse,
    responses=error_responses,
)
async def get_active_employee_schedule(
    employee_id: UUID,
    session: SessionDep,
) -> WorkScheduleResponse:
    try:
        schedule = await WorkScheduleService(session).get_active(employee_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return WorkScheduleResponse.model_validate(schedule)


@router.get(
    "/{employee_id}/schedule-diagnostics",
    response_model=ScheduleDiagnosticsResponse,
    responses=error_responses,
)
async def get_employee_schedule_diagnostics(
    employee_id: UUID,
    session: SessionDep,
    _current_employee: Annotated[
        Employee,
        Depends(
            require_roles_or_self_employee(
                EmployeeRole.ADMIN, EmployeeRole.HR, EmployeeRole.MANAGER
            )
        ),
    ],
) -> ScheduleDiagnosticsResponse:
    try:
        return await ScheduleDiagnosticsService(session).get_for_employee(employee_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.get(
    "/{employee_id}/schedules/history",
    response_model=list[ChangeHistoryResponse],
    responses=error_responses,
)
async def list_employee_schedule_history(
    employee_id: UUID,
    session: SessionDep,
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[ChangeHistoryResponse]:
    try:
        await EmployeeService(session).get(employee_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    entries = await ChangeHistoryRepository(session).list_for_employee(
        employee_id,
        entity_type=ENTITY_WORK_SCHEDULE,
        skip=skip,
        limit=limit,
    )
    return [ChangeHistoryResponse.model_validate(entry) for entry in entries]


@router.post(
    "/{employee_id}/exceptions",
    response_model=ScheduleExceptionResponse,
    status_code=status.HTTP_201_CREATED,
    responses=error_responses,
)
async def create_employee_exception(
    employee_id: UUID,
    payload: ScheduleExceptionCreate,
    session: SessionDep,
    current_employee: Annotated[
        Employee,
        Depends(
            require_roles_or_self_employee(
                EmployeeRole.ADMIN, EmployeeRole.HR, EmployeeRole.MANAGER
            )
        ),
    ],
) -> ScheduleExceptionResponse:
    try:
        schedule_exception = await ScheduleExceptionService(session).create(
            employee_id, payload, changed_by=current_employee.id
        )
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvalidOperationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return ScheduleExceptionResponse.model_validate(schedule_exception)


@router.get(
    "/{employee_id}/exceptions",
    response_model=list[ScheduleExceptionResponse],
    responses=error_responses,
)
async def list_employee_exceptions(
    employee_id: UUID,
    session: SessionDep,
) -> list[ScheduleExceptionResponse]:
    try:
        schedule_exceptions = await ScheduleExceptionService(session).list_for_employee(employee_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return [
        ScheduleExceptionResponse.model_validate(schedule_exception)
        for schedule_exception in schedule_exceptions
    ]


@router.patch(
    "/{employee_id}/exceptions/{exception_id}",
    response_model=ScheduleExceptionResponse,
    responses=error_responses,
)
async def update_employee_exception(
    employee_id: UUID,
    exception_id: UUID,
    payload: ScheduleExceptionUpdate,
    session: SessionDep,
    current_employee: Annotated[
        Employee,
        Depends(
            require_roles_or_self_employee(
                EmployeeRole.ADMIN, EmployeeRole.HR, EmployeeRole.MANAGER
            )
        ),
    ],
) -> ScheduleExceptionResponse:
    try:
        schedule_exception = await ScheduleExceptionService(session).update(
            employee_id, exception_id, payload, changed_by=current_employee.id
        )
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvalidOperationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return ScheduleExceptionResponse.model_validate(schedule_exception)


@router.delete(
    "/{employee_id}/exceptions/{exception_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=error_responses,
)
async def delete_employee_exception(
    employee_id: UUID,
    exception_id: UUID,
    session: SessionDep,
    current_employee: Annotated[
        Employee,
        Depends(
            require_roles_or_self_employee(
                EmployeeRole.ADMIN, EmployeeRole.HR, EmployeeRole.MANAGER
            )
        ),
    ],
) -> None:
    try:
        await ScheduleExceptionService(session).delete(
            employee_id, exception_id, changed_by=current_employee.id
        )
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc



@router.get(
    "/{employee_id}/exceptions/history",
    response_model=list[ChangeHistoryResponse],
    responses=error_responses,
)
async def list_employee_exception_history(
    employee_id: UUID,
    session: SessionDep,
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[ChangeHistoryResponse]:
    try:
        await EmployeeService(session).get(employee_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    entries = await ChangeHistoryRepository(session).list_for_employee(
        employee_id,
        entity_type=ENTITY_SCHEDULE_EXCEPTION,
        skip=skip,
        limit=limit,
    )
    return [ChangeHistoryResponse.model_validate(entry) for entry in entries]


@router.get(
    "/{employee_id}/history",
    response_model=list[ChangeHistoryResponse],
    responses=error_responses,
)
async def list_employee_history(
    employee_id: UUID,
    session: SessionDep,
    entity_type: str | None = Query(default=None, max_length=40),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[ChangeHistoryResponse]:
    try:
        await EmployeeService(session).get(employee_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    entries = await ChangeHistoryRepository(session).list_for_employee(
        employee_id,
        entity_type=entity_type,
        skip=skip,
        limit=limit,
    )
    return [ChangeHistoryResponse.model_validate(entry) for entry in entries]


@router.post(
    "/{employee_id}/schedule/confirm",
    response_model=ScheduleConfirmResponse,
    responses=error_responses,
)
async def confirm_employee_schedule(
    employee_id: UUID,
    session: SessionDep,
    current_employee: Annotated[
        Employee,
        Depends(require_roles_or_self_employee(*MANAGEMENT_ROLES)),
    ],
) -> ScheduleConfirmResponse:
    del current_employee
    try:
        return await ScheduleConfirmationService(session).confirm(employee_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post(
    "/schedule/confirmation-requests/bulk",
    response_model=BulkScheduleConfirmationRequestResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        **error_responses,
        status.HTTP_403_FORBIDDEN: {"model": ErrorResponse},
    },
)
async def create_bulk_schedule_confirmation_requests(
    payload: BulkScheduleConfirmationRequestCreate,
    session: SessionDep,
    current_employee: Annotated[
        Employee,
        Depends(require_roles(*MANAGEMENT_ROLES)),
    ],
) -> BulkScheduleConfirmationRequestResponse:
    created, skipped = await ScheduleConfirmationService(session).create_bulk(
        employee_ids=payload.employee_ids,
        requested_by_id=current_employee.id,
        reason=payload.reason,
    )
    return BulkScheduleConfirmationRequestResponse(
        created=[schedule_confirmation_to_response(request) for request in created],
        skipped_employee_ids=skipped,
    )


@router.post(
    "/{employee_id}/schedule/confirmation-requests",
    response_model=ScheduleConfirmationRequestResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        **error_responses,
        status.HTTP_403_FORBIDDEN: {"model": ErrorResponse},
        status.HTTP_409_CONFLICT: {"model": ErrorResponse},
    },
)
async def create_schedule_confirmation_request(
    employee_id: UUID,
    payload: ScheduleConfirmationRequestCreate,
    session: SessionDep,
    current_employee: Annotated[
        Employee,
        Depends(require_roles(*MANAGEMENT_ROLES)),
    ],
) -> ScheduleConfirmationRequestResponse:
    try:
        request, created = await ScheduleConfirmationService(session).create_request(
            employee_id=employee_id,
            requested_by_id=current_employee.id,
            reason=payload.reason,
        )
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    response = schedule_confirmation_to_response(request)
    if not created:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="pending request already exists",
        )
    return response


@router.get(
    "/{employee_id}/schedule/confirmation-requests",
    response_model=list[ScheduleConfirmationRequestResponse],
    responses=error_responses,
)
async def list_schedule_confirmation_requests(
    employee_id: UUID,
    session: SessionDep,
    _current_employee: CurrentEmployeeDep,
    status_filter: Literal["pending", "confirmed", "declined"] | None = Query(
        default=None, alias="status"
    ),
) -> list[ScheduleConfirmationRequestResponse]:
    try:
        requests = await ScheduleConfirmationService(session).list_requests(
            employee_id, status_filter=status_filter
        )
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return [schedule_confirmation_to_response(request) for request in requests]


@router.post(
    "/{employee_id}/schedule/confirmation-requests/{request_id}/decline",
    response_model=ScheduleConfirmationRequestResponse,
    responses={
        **error_responses,
        status.HTTP_403_FORBIDDEN: {"model": ErrorResponse},
    },
)
async def decline_schedule_confirmation_request(
    employee_id: UUID,
    request_id: UUID,
    payload: ScheduleConfirmDeclineRequest,
    session: SessionDep,
    current_employee: CurrentEmployeeDep,
) -> ScheduleConfirmationRequestResponse:
    if current_employee.id != employee_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="only target employee can decline the request",
        )
    try:
        request = await ScheduleConfirmationService(session).decline(
            employee_id=employee_id,
            request_id=request_id,
            note=payload.note,
        )
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvalidOperationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return schedule_confirmation_to_response(request)
