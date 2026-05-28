from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.availability import WorkScheduleWindow
from app.analytics.meeting import EventInterval, busy_hours, count_outside_events
from app.analytics.metrics import (
    actuality_score,
    conflict_rate,
    days_since_update,
    hr_factor,
    load_level,
    risk_level,
    risk_score,
    zone_factor,
)
from app.analytics.recurrence import expand_event
from app.models.employee import Employee
from app.models.employee_metric import EmployeeMetric
from app.models.employee_metric_snapshot import EmployeeMetricSnapshot
from app.models.schedule_exception import ScheduleException
from app.models.work_schedule import WorkSchedule
from app.repositories.activity_events import ActivityEventRepository
from app.repositories.employee_metric_snapshots import EmployeeMetricSnapshotRepository
from app.repositories.employee_metrics import EmployeeMetricRepository
from app.repositories.employees import EmployeeRepository
from app.repositories.schedule_exceptions import ScheduleExceptionRepository
from app.repositories.work_schedules import WorkScheduleRepository
from app.services.notification_triggers import maybe_emit_for_metric
from app.services.notifications import NotificationService

DEFAULT_WINDOW_DAYS = 14
HR_EVENT_SOURCES = frozenset({"hr", "timesheet"})
CALENDAR_EVENT_SOURCES = frozenset({"calendar", "google_calendar", "outlook", "ical"})
ABSENCE_TYPES = frozenset({"vacation", "sick", "sick_leave", "business_trip", "personal_hours"})


class MetricCalculatorService:
    """Пересчитывает EmployeeMetric для одного или всех сотрудников.

    Реализует пайплайн §5–§10 ТЗ: Ai, Ci, Li, Zi, Hi → Ri, risk_level.
    """

    def __init__(
        self,
        session: AsyncSession,
        *,
        notifications: NotificationService | None = None,
    ) -> None:
        self.session = session
        self.employees = EmployeeRepository(session)
        self.metrics = EmployeeMetricRepository(session)
        self.snapshots = EmployeeMetricSnapshotRepository(session)
        self.events = ActivityEventRepository(session)
        self.schedules = WorkScheduleRepository(session)
        self.exceptions = ScheduleExceptionRepository(session)
        self.notifications = notifications or NotificationService(session)

    async def recompute_all(
        self,
        *,
        today: date | None = None,
        window_days: int = DEFAULT_WINDOW_DAYS,
    ) -> int:
        reference_date = today or datetime.now(UTC).date()
        batch_taken_at = datetime.now(UTC)
        employees = await self.employees.list()
        for employee in employees:
            await self._recompute_for_employee(
                employee=employee,
                today=reference_date,
                window_days=window_days,
                taken_at=batch_taken_at,
            )
        await self.session.commit()
        return len(employees)

    async def recompute_for_employee_id(
        self,
        employee_id: UUID,
        *,
        today: date | None = None,
        window_days: int = DEFAULT_WINDOW_DAYS,
    ) -> EmployeeMetric | None:
        employee = await self.employees.get(employee_id)
        if employee is None:
            return None
        reference_date = today or datetime.now(UTC).date()
        metric = await self._recompute_for_employee(
            employee=employee,
            today=reference_date,
            window_days=window_days,
            taken_at=datetime.now(UTC),
        )
        await self.session.commit()
        return metric

    async def _recompute_for_employee(
        self,
        *,
        employee: Employee,
        today: date,
        window_days: int,
        taken_at: datetime,
    ) -> EmployeeMetric:
        window_end = datetime.combine(today, datetime.min.time(), tzinfo=UTC) + timedelta(days=1)
        window_start = window_end - timedelta(days=window_days)

        schedule = await self.schedules.get_active_for_employee(employee.id)
        events = await self.events.list_for_employees_in_range(
            [employee.id],
            window_start,
            window_end,
        )
        exceptions = await self.exceptions.list_for_employees_in_range(
            [employee.id],
            window_start,
            window_end,
        )

        last_updated_at = schedule.last_updated_at if schedule is not None else employee.created_at
        days = days_since_update(last_updated_at, today)
        actuality = actuality_score(days)

        schedule_window = _schedule_window(schedule)
        # Разворачиваем повторяющиеся события в occurrences внутри окна —
        # без этого Ci/Li занижены для команд с регулярными митингами (§18 ТЗ).
        intervals: list[EventInterval] = []
        for event in events:
            intervals.extend(expand_event(event, window_start, window_end))
        outside_count = count_outside_events(intervals, schedule_window)
        conflict = conflict_rate(outside_count, len(intervals))

        busy = busy_hours(intervals)
        work_hours_value = _expected_work_hours(schedule, exceptions, window_start, window_end)
        load = load_level(busy, work_hours_value)

        zone = zone_factor(employee.timezone, (event.timezone for event in events))
        hr_count = sum(1 for event in events if event.source in HR_EVENT_SOURCES)
        cal_count = sum(1 for event in events if event.source in CALENDAR_EVENT_SOURCES)
        hr = hr_factor(hr_count, cal_count)

        score = risk_score(
            actuality_score_value=actuality,
            conflict_rate_value=conflict,
            load_level_value=load,
            zone_factor_value=zone,
            hr_factor_value=hr,
        )
        level = risk_level(score)

        # Запоминаем предыдущую метрику ДО upsert: триггеру нужно сравнение
        # старого и нового risk_level, чтобы понять «вырос ли риск».
        previous = await self.metrics.get_for_employee(employee.id)
        previous_snapshot = _shallow_metric_copy(previous) if previous else None

        metric = EmployeeMetric(
            id=uuid4(),
            employee_id=employee.id,
            calculated_at=taken_at,
            days_since_update=days,
            actuality_score=actuality,
            outside_events_count=outside_count,
            total_events_count=len(intervals),
            conflict_rate=conflict,
            load_level=load,
            zone_factor=zone,
            hr_factor=hr,
            risk_score=score,
            risk_level=level,
        )
        saved = await self.metrics.upsert(metric)
        await self.snapshots.add(_snapshot_from_metric(saved, taken_at))

        # Smart-уведомления (§16 п.6): triggered by metric, not by user action.
        # Сбои здесь не должны валить пересчёт метрик — поэтому ловим всё.
        try:
            await maybe_emit_for_metric(
                employee=employee,
                previous=previous_snapshot,
                current=saved,
                notifications=self.notifications,
                now=taken_at,
            )
        except Exception:  # noqa: BLE001
            import logging

            logging.getLogger(__name__).exception(
                "maybe_emit_for_metric failed for employee_id=%s", employee.id
            )

        return saved


def _schedule_window(schedule: WorkSchedule | None) -> WorkScheduleWindow | None:
    if schedule is None:
        return None
    return WorkScheduleWindow(
        work_days=tuple(schedule.work_days),
        start_time=schedule.start_time,
        end_time=schedule.end_time,
    )


def _shallow_metric_copy(metric: EmployeeMetric) -> EmployeeMetric:
    """Снимок значений до upsert: сам upsert мутирует существующую row в ORM,
    поэтому без detached-копии триггер увидит уже новые risk_level/Ai/Ci."""
    return EmployeeMetric(
        id=metric.id,
        employee_id=metric.employee_id,
        calculated_at=metric.calculated_at,
        days_since_update=metric.days_since_update,
        actuality_score=metric.actuality_score,
        outside_events_count=metric.outside_events_count,
        total_events_count=metric.total_events_count,
        conflict_rate=metric.conflict_rate,
        load_level=metric.load_level,
        zone_factor=metric.zone_factor,
        hr_factor=metric.hr_factor,
        risk_score=metric.risk_score,
        risk_level=metric.risk_level,
    )


def _snapshot_from_metric(
    metric: EmployeeMetric,
    taken_at: datetime,
) -> EmployeeMetricSnapshot:
    return EmployeeMetricSnapshot(
        id=uuid4(),
        employee_id=metric.employee_id,
        taken_at=taken_at,
        days_since_update=metric.days_since_update,
        actuality_score=metric.actuality_score,
        outside_events_count=metric.outside_events_count,
        total_events_count=metric.total_events_count,
        conflict_rate=metric.conflict_rate,
        load_level=metric.load_level,
        zone_factor=metric.zone_factor,
        hr_factor=metric.hr_factor,
        risk_score=metric.risk_score,
        risk_level=metric.risk_level,
    )


def _expected_work_hours(
    schedule: WorkSchedule | None,
    exceptions: list[ScheduleException],
    window_start: datetime,
    window_end: datetime,
) -> float:
    if schedule is None:
        return 0.0
    start_seconds = schedule.start_time.hour * 3600 + schedule.start_time.minute * 60
    end_seconds = schedule.end_time.hour * 3600 + schedule.end_time.minute * 60
    daily_hours = max(0.0, (end_seconds - start_seconds) / 3600)
    if daily_hours == 0.0:
        return 0.0

    absence_days = _count_absence_days(exceptions, window_start, window_end)
    work_days_set = set(schedule.work_days)
    total_work_days = 0
    current = window_start.date()
    end = window_end.date()
    while current < end:
        if current.weekday() in work_days_set:
            total_work_days += 1
        current += timedelta(days=1)
    effective_days = max(0, total_work_days - absence_days)
    return effective_days * daily_hours


def _count_absence_days(
    exceptions: list[ScheduleException],
    window_start: datetime,
    window_end: datetime,
) -> int:
    days: set[date] = set()
    window_end_exclusive = window_end.date()
    for item in exceptions:
        if item.type not in ABSENCE_TYPES:
            continue
        start = max(item.start_dt, window_start).date()
        end = min(item.end_dt, window_end).date()
        current = start
        while current <= end and current < window_end_exclusive:
            days.add(current)
            current += timedelta(days=1)
    return len(days)
