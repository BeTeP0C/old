from typing import Annotated, Any
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentEmployeeDep, get_db_session, require_roles
from app.core.roles import MANAGEMENT_ROLES, EmployeeRole
from app.importers.activity_events import (
    ActivityEventImportValidationError,
    parse_csv_activity_events,
    parse_json_activity_events,
)
from app.models.employee import Employee
from app.models.import_log import ImportLog
from app.repositories.import_logs import ImportLogRepository
from app.schemas.activity_event import (
    ActivityEventCreate,
    ActivityEventImportResult,
    ActivityEventResponse,
)
from app.schemas.common import ErrorResponse
from app.schemas.import_log import ImportLogResponse
from app.services.activity_events import ActivityEventService
from app.services.exceptions import InvalidOperationError, NotFoundError

router = APIRouter(tags=["activity events"])
SessionDep = Annotated[AsyncSession, Depends(get_db_session)]
CsvFileDep = Annotated[UploadFile, File(...)]

error_responses: dict[int | str, dict[str, Any]] = {
    status.HTTP_400_BAD_REQUEST: {"model": ErrorResponse},
    status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
}


def _status_from_result(result: ActivityEventImportResult) -> str:
    if result.imported_count > 0:
        return "ok"
    if result.skipped_duplicate_count > 0:
        # Файл прочитан, но всё — дубли (новых событий не добавлено).
        return "partial"
    # Ничего не импортировано и нет дублей — пустой/бессмысленный файл.
    return "error"


async def _record_import_log(
    session: AsyncSession,
    *,
    source: str | None,
    file_name: str | None,
    created_by: UUID,
    status_: str,
    imported: int = 0,
    skipped: int = 0,
    errors: int = 0,
    content: str | None = None,
) -> None:
    """Пишет строку аудита в import_logs (для UI «Последние загрузки»).

    Вызывается и при успехе, и при ошибке. Коммитит отдельной транзакцией: к
    этому моменту import_events уже либо закоммитил события, либо откатил их.
    content — исходный текст файла (для скачивания); has_file ставится, если он есть.
    """
    await ImportLogRepository(session).create(
        ImportLog(
            source=source or "unknown",
            file_name=file_name,
            status=status_,
            imported_count=imported,
            skipped_duplicate_count=skipped,
            error_count=errors,
            created_by=created_by,
            content=content,
            has_file=content is not None,
        )
    )
    await session.commit()


@router.post(
    "/import/events/csv",
    response_model=ActivityEventImportResult,
    responses=error_responses,
)
async def import_activity_events_csv(
    session: SessionDep,
    file: CsvFileDep,
    current_employee: Annotated[
        Employee,
        Depends(require_roles(*MANAGEMENT_ROLES)),
    ],
    source: Annotated[
        str | None,
        Query(
            description=(
                "Источник по умолчанию для строк без поля source: "
                "calendar | hr | tracker | timesheet"
            ),
            max_length=60,
        ),
    ] = None,
) -> ActivityEventImportResult:
    file_name = file.filename
    try:
        content = (await file.read()).decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        # Не UTF-8 (например, Windows-1251 из Excel) — иначе был бы непонятный 500.
        await _record_import_log(
            session, source=source, file_name=file_name,
            created_by=current_employee.id, status_="error", errors=1,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="файл должен быть в кодировке UTF-8 — пересохраните CSV в UTF-8 и загрузите снова",
        ) from exc
    try:
        events = parse_csv_activity_events(content, default_source=source)
        result = await ActivityEventService(session).import_events(events)
    except ActivityEventImportValidationError as exc:
        # Сохраняем исходник даже у битого файла — чтобы можно было скачать,
        # поправить и загрузить снова.
        await _record_import_log(
            session, source=source, file_name=file_name,
            created_by=current_employee.id, status_="error", errors=len(exc.errors),
            content=content,
        )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=exc.errors) from exc
    except InvalidOperationError as exc:
        await _record_import_log(
            session, source=source, file_name=file_name,
            created_by=current_employee.id, status_="error", errors=1, content=content,
        )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    await _record_import_log(
        session, source=source, file_name=file_name, created_by=current_employee.id,
        status_=_status_from_result(result), imported=result.imported_count,
        skipped=result.skipped_duplicate_count, content=content,
    )
    return result


@router.post(
    "/import/events/json",
    response_model=ActivityEventImportResult,
    responses=error_responses,
)
async def import_activity_events_json(
    payload: list[dict[str, object]],
    session: SessionDep,
    current_employee: Annotated[
        Employee,
        Depends(require_roles(*MANAGEMENT_ROLES)),
    ],
    source: Annotated[
        str | None,
        Query(
            description=(
                "Источник по умолчанию для элементов без поля source: "
                "calendar | hr | tracker | timesheet"
            ),
            max_length=60,
        ),
    ] = None,
) -> ActivityEventImportResult:
    # У JSON-импорта нет имени файла (это может быть генерация тестовых данных).
    try:
        events = parse_json_activity_events(payload, default_source=source)
        result = await ActivityEventService(session).import_events(events)
    except ActivityEventImportValidationError as exc:
        await _record_import_log(
            session, source=source, file_name=None,
            created_by=current_employee.id, status_="error", errors=len(exc.errors),
        )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=exc.errors) from exc
    except InvalidOperationError as exc:
        await _record_import_log(
            session, source=source, file_name=None,
            created_by=current_employee.id, status_="error", errors=1,
        )
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    await _record_import_log(
        session, source=source, file_name=None, created_by=current_employee.id,
        status_=_status_from_result(result), imported=result.imported_count,
        skipped=result.skipped_duplicate_count,
    )
    return result


@router.get("/import/history", response_model=list[ImportLogResponse])
async def list_import_history(
    session: SessionDep,
    response: Response,
    _current_employee: Annotated[
        Employee,
        Depends(require_roles(*MANAGEMENT_ROLES)),
    ],
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
) -> list[ImportLogResponse]:
    repo = ImportLogRepository(session)
    # X-Total-Count — для пагинации на фронте (как в /employees).
    response.headers["X-Total-Count"] = str(await repo.count())
    logs = await repo.list(skip=skip, limit=limit)
    return [ImportLogResponse.model_validate(log) for log in logs]


@router.get(
    "/import/history/{import_id}/download",
    responses={**error_responses, status.HTTP_404_NOT_FOUND: {"model": ErrorResponse}},
)
async def download_import_file(
    import_id: UUID,
    session: SessionDep,
    _current_employee: Annotated[
        Employee,
        Depends(require_roles(*MANAGEMENT_ROLES)),
    ],
) -> Response:
    log = await ImportLogRepository(session).get_with_content(import_id)
    if log is None or log.content is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="исходный файл для этой загрузки недоступен",
        )
    name = log.file_name or f"import-{import_id}.csv"
    media_type = "application/json" if name.lower().endswith(".json") else "text/csv"
    # filename* (RFC 5987) — корректно отдаёт имена с кириллицей.
    disposition = f"attachment; filename*=UTF-8''{quote(name)}"
    return Response(
        content=log.content,
        media_type=f"{media_type}; charset=utf-8",
        headers={"Content-Disposition": disposition},
    )


@router.post(
    "/events/manual",
    response_model=ActivityEventResponse,
    status_code=status.HTTP_201_CREATED,
    responses=error_responses,
)
async def create_manual_activity_event(
    payload: ActivityEventCreate,
    session: SessionDep,
    current_employee: CurrentEmployeeDep,
) -> ActivityEventResponse:
    privileged = {
        EmployeeRole.ADMIN.value,
        EmployeeRole.HR.value,
        EmployeeRole.PM.value,
    }
    if current_employee.role not in privileged and current_employee.id != payload.employee_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="insufficient permissions",
        )
    try:
        event = await ActivityEventService(session).create_manual(
            payload, actor_id=current_employee.id
        )
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvalidOperationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return ActivityEventResponse.model_validate(event)


@router.get(
    "/employees/{employee_id}/events",
    response_model=list[ActivityEventResponse],
    responses=error_responses,
)
async def list_employee_activity_events(
    employee_id: UUID,
    session: SessionDep,
) -> list[ActivityEventResponse]:
    try:
        events = await ActivityEventService(session).list_for_employee(employee_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return [ActivityEventResponse.model_validate(event) for event in events]
