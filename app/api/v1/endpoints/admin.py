import time
from dataclasses import asdict
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentEmployeeDep, get_db_session, require_roles
from app.core.config import settings
from app.core.roles import EmployeeRole
from app.models.employee import Employee
from app.models.import_log import ImportLog
from app.repositories.import_logs import ImportLogRepository
from app.services.metric_calculator import DEFAULT_WINDOW_DAYS, MetricCalculatorService

router = APIRouter(prefix="/admin", tags=["admin"])
SessionDep = Annotated[AsyncSession, Depends(get_db_session)]


class RecomputeMetricsResponse(BaseModel):
    processed_count: int
    took_ms: int
    window_days: int


@router.post("/recompute-metrics", response_model=RecomputeMetricsResponse)
async def recompute_metrics(
    session: SessionDep,
    _current_employee: CurrentEmployeeDep,
    window_days: int = DEFAULT_WINDOW_DAYS,
) -> RecomputeMetricsResponse:
    """Пересчитывает EmployeeMetric для всех сотрудников.

    Считает Ai, Ci, Li, Zi, Hi → Ri и UPSERT-ит строку в employee_metrics.
    Окно анализа фактических событий задаётся параметром `window_days`.
    """
    started_at = time.perf_counter()
    processed_count = await MetricCalculatorService(session).recompute_all(window_days=window_days)
    took_ms = int((time.perf_counter() - started_at) * 1000)
    return RecomputeMetricsResponse(
        processed_count=processed_count,
        took_ms=took_ms,
        window_days=window_days,
    )


class SeedDemoRequest(BaseModel):
    small: bool = True
    reset: bool = True
    with_roadmap: bool = True


class SeedDemoResponse(BaseModel):
    employees_created: int
    teams_created: int
    schedules_created: int
    events_created: int
    metrics_created: int
    snapshots_created: int
    confirmation_requests_created: int
    roadmap_created: int
    roadmap_skipped: int
    took_ms: int


@router.post("/seed-demo", response_model=SeedDemoResponse)
async def seed_demo(
    payload: SeedDemoRequest,
    session: SessionDep,
    actor: Annotated[Employee, Depends(require_roles(EmployeeRole.ADMIN))],
) -> SeedDemoResponse:
    """Создаёт демо-набор данных. Доступно только при APP_DEBUG=true."""
    if not settings.debug:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Seed endpoint is disabled (APP_DEBUG=false)",
        )
    # Импорт здесь, чтобы scripts/seed.py не тащился в импорт-граф при APP_DEBUG=false.
    from scripts.seed import run_seed

    started_at = time.perf_counter()
    result = await run_seed(
        session,
        reset=payload.reset,
        small=payload.small,
        from_files=False,
        with_roadmap=payload.with_roadmap,
    )
    took_ms = int((time.perf_counter() - started_at) * 1000)
    # Фиксируем в журнале загрузок, чтобы запись о генерации демо-данных
    # переживала перезагрузку страницы /upload (как и обычные импорты).
    await ImportLogRepository(session).create(
        ImportLog(
            source="seed",
            file_name="Демо-набор",
            status="ok",
            imported_count=result.events_created,
            skipped_duplicate_count=0,
            error_count=0,
            created_by=actor.id,
        )
    )
    await session.commit()
    return SeedDemoResponse(**asdict(result), took_ms=took_ms)
