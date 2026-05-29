from collections import defaultdict
from collections.abc import Iterable
from datetime import date, datetime, time, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.availability import (
    EmployeeAvailabilityInput,
    MeetingRecommendation,
    TimeWindow,
    calculate_employee_availability,
    recommend_meeting_windows,
)
from app.analytics.recurrence import expand_event
from app.models.employee import Employee
from app.models.work_schedule import WorkSchedule
from app.repositories.activity_events import ActivityEventRepository
from app.repositories.employee_metrics import EmployeeMetricRepository
from app.repositories.employees import EmployeeRepository
from app.repositories.schedule_exceptions import ScheduleExceptionRepository
from app.repositories.team_members import TeamMemberRepository
from app.repositories.teams import TeamRepository
from app.repositories.work_schedules import WorkScheduleRepository
from app.schemas.availability import (
    AvailabilityWindowResponse,
    EmployeeAvailabilityResponse,
    EmployeeLocalTimeResponse,
    MeetingRecommendationRequest,
    MeetingRecommendationResponse,
    TeamAvailabilityResponse,
)
from app.services.exceptions import InvalidOperationError, NotFoundError

EXCLUDED_EXCEPTION_TYPES = {"vacation", "sick", "sick_leave", "personal_hours"}


class TeamAvailabilityService:
    def __init__(self, session: AsyncSession) -> None:
        self.teams = TeamRepository(session)
        self.team_members = TeamMemberRepository(session)
        self.employees = EmployeeRepository(session)
        self.schedules = WorkScheduleRepository(session)
        self.exceptions = ScheduleExceptionRepository(session)
        self.events = ActivityEventRepository(session)
        self.metrics = EmployeeMetricRepository(session)

    async def get_availability(
        self,
        team_id: UUID,
        range_start: datetime,
        range_end: datetime,
    ) -> TeamAvailabilityResponse:
        if range_start >= range_end:
            raise InvalidOperationError("range_start must be earlier than range_end")
        employees, inputs = await self._build_availability_inputs(team_id, range_start, range_end)
        availability_by_employee = {
            UUID(item.employee_id): item
            for item in (
                calculate_employee_availability(
                    employee_input,
                    range_start=range_start,
                    range_end=range_end,
                )
                for employee_input in inputs
            )
        }

        return TeamAvailabilityResponse(
            team_id=team_id,
            range_start=range_start,
            range_end=range_end,
            employees=[
                EmployeeAvailabilityResponse(
                    employee_id=employee.id,
                    timezone=employee.timezone,
                    available_windows=[
                        AvailabilityWindowResponse(
                            start_dt=window.start_dt,
                            end_dt=window.end_dt,
                        )
                        for window in availability_by_employee[employee.id].available_windows
                    ],
                )
                for employee in employees
            ],
        )

    async def get_employee_availability(
        self,
        employee_id: UUID,
        range_start: datetime,
        range_end: datetime,
    ) -> list[TimeWindow]:
        """Свободные окна одного сотрудника за период (для AI-контекста «когда
        свободен X»). Считает рабочий график минус отпуска/больничные и события."""
        if range_start >= range_end:
            raise InvalidOperationError("range_start must be earlier than range_end")
        if await self.employees.get(employee_id) is None:
            raise NotFoundError("employee not found")

        schedules = await self.schedules.list_active_for_employees([employee_id])
        exceptions = await self.exceptions.list_for_employees_in_range(
            [employee_id], range_start, range_end
        )
        events = await self.events.list_for_employees_in_range(
            [employee_id], range_start, range_end
        )
        employee_input = EmployeeAvailabilityInput(
            employee_id=str(employee_id),
            schedule_windows=tuple(
                window
                for schedule in schedules
                for window in _schedule_windows_for_range(schedule, range_start, range_end)
            ),
            busy_windows=tuple(
                [
                    *(
                        TimeWindow(item.start_dt, item.end_dt)
                        for item in exceptions
                        if item.type in EXCLUDED_EXCEPTION_TYPES
                    ),
                    *(
                        TimeWindow(interval.start_dt, interval.end_dt)
                        for item in events
                        for interval in expand_event(item, range_start, range_end)
                    ),
                ]
            ),
        )
        availability = calculate_employee_availability(
            employee_input,
            range_start=range_start,
            range_end=range_end,
        )
        return list(availability.available_windows)

    async def recommend_meetings(
        self,
        team_id: UUID,
        payload: MeetingRecommendationRequest,
    ) -> list[MeetingRecommendationResponse]:
        if payload.start_dt >= payload.end_dt:
            raise InvalidOperationError("start_dt must be earlier than end_dt")
        employees, inputs = await self._build_availability_inputs(
            team_id,
            payload.start_dt,
            payload.end_dt,
        )
        team_member_ids = {employee.id for employee in employees}
        _validate_membership(payload.required_employee_ids, team_member_ids, "required")
        _validate_membership(payload.optional_employee_ids, team_member_ids, "optional")

        if payload.required_employee_ids:
            required_uuids = set(payload.required_employee_ids)
        elif payload.optional_employee_ids:
            required_uuids = set()
        else:
            required_uuids = set(team_member_ids)
        optional_uuids = set(payload.optional_employee_ids) - required_uuids

        metrics = await self.metrics.list_for_employees([employee.id for employee in employees])
        load_by_employee = {metric.employee_id: metric.load_level for metric in metrics}
        overloaded_uuids = {
            employee.id
            for employee in employees
            if load_by_employee.get(employee.id, 0.0) > payload.load_threshold
        }

        availability = [
            calculate_employee_availability(
                employee_input,
                range_start=payload.start_dt,
                range_end=payload.end_dt,
            )
            for employee_input in inputs
        ]
        recommendations = recommend_meeting_windows(
            availability,
            range_start=payload.start_dt,
            range_end=payload.end_dt,
            duration_minutes=payload.duration_minutes,
            required_ids=frozenset(str(eid) for eid in required_uuids),
            optional_ids=frozenset(str(eid) for eid in optional_uuids),
            overloaded_ids=frozenset(str(eid) for eid in overloaded_uuids),
        )
        employee_by_id = {employee.id: employee for employee in employees}
        return [
            _to_recommendation_response(
                recommendation,
                employee_by_id=employee_by_id,
                required_uuids=required_uuids,
                optional_uuids=optional_uuids,
            )
            for recommendation in recommendations
        ]

    async def _build_availability_inputs(
        self,
        team_id: UUID,
        range_start: datetime,
        range_end: datetime,
    ) -> tuple[list[Employee], list[EmployeeAvailabilityInput]]:
        if await self.teams.get(team_id) is None:
            raise NotFoundError("team not found")

        employee_ids = await self.team_members.list_employee_ids_for_team(team_id)
        employees = await self.employees.list_by_ids(employee_ids)
        schedules = await self.schedules.list_active_for_employees(employee_ids)
        exceptions = await self.exceptions.list_for_employees_in_range(
            employee_ids,
            range_start,
            range_end,
        )
        events = await self.events.list_for_employees_in_range(employee_ids, range_start, range_end)

        schedules_by_employee = _group_by_employee(schedules)
        exceptions_by_employee = _group_by_employee(exceptions)
        events_by_employee = _group_by_employee(events)
        return employees, [
            EmployeeAvailabilityInput(
                employee_id=str(employee.id),
                schedule_windows=tuple(
                    window
                    for schedule in schedules_by_employee[employee.id]
                    for window in _schedule_windows_for_range(schedule, range_start, range_end)
                ),
                busy_windows=tuple(
                    [
                        *(
                            TimeWindow(item.start_dt, item.end_dt)
                            for item in exceptions_by_employee[employee.id]
                            if item.type in EXCLUDED_EXCEPTION_TYPES
                        ),
                        *(
                            TimeWindow(interval.start_dt, interval.end_dt)
                            for item in events_by_employee[employee.id]
                            for interval in expand_event(item, range_start, range_end)
                        ),
                    ]
                ),
            )
            for employee in employees
        ]


def _group_by_employee(items: Iterable[Any]) -> defaultdict[UUID, list[Any]]:
    grouped: defaultdict[UUID, list[Any]] = defaultdict(list)
    for item in items:
        grouped[item.employee_id].append(item)
    return grouped


def _schedule_windows_for_range(
    schedule: WorkSchedule,
    range_start: datetime,
    range_end: datetime,
) -> list[TimeWindow]:
    timezone = ZoneInfo(schedule.timezone)
    current_date = range_start.astimezone(timezone).date()
    end_date = range_end.astimezone(timezone).date()
    windows: list[TimeWindow] = []
    while current_date <= end_date:
        if current_date.weekday() in schedule.work_days:
            window = _local_window(current_date, schedule.start_time, schedule.end_time, timezone)
            if window.end_dt > range_start and window.start_dt < range_end:
                windows.append(window)
        current_date += timedelta(days=1)
    return windows


def _local_window(day: date, start_time: time, end_time: time, timezone: ZoneInfo) -> TimeWindow:
    return TimeWindow(
        start_dt=datetime.combine(day, start_time, tzinfo=timezone),
        end_dt=datetime.combine(day, end_time, tzinfo=timezone),
    )


def _validate_membership(
    ids: list[UUID],
    team_member_ids: set[UUID],
    field_label: str,
) -> None:
    unknown = [str(eid) for eid in ids if eid not in team_member_ids]
    if unknown:
        raise InvalidOperationError(
            f"{field_label} employees not in team: {', '.join(unknown)}"
        )


def _to_recommendation_response(
    recommendation: MeetingRecommendation,
    *,
    employee_by_id: dict[UUID, Employee],
    required_uuids: set[UUID],
    optional_uuids: set[UUID],
) -> MeetingRecommendationResponse:
    required_available = [UUID(eid) for eid in recommendation.required_available_ids]
    required_missing = [UUID(eid) for eid in recommendation.required_missing_ids]
    optional_available = [UUID(eid) for eid in recommendation.optional_available_ids]
    optional_missing = [UUID(eid) for eid in recommendation.optional_missing_ids]
    overloaded = [UUID(eid) for eid in recommendation.overloaded_employee_ids]

    participant_uuids = list((required_uuids | optional_uuids) - set(overloaded))
    participant_uuids.sort(key=str)
    local_times = [
        _build_local_time(
            employee_by_id[employee_uuid],
            recommendation.start_dt,
            recommendation.end_dt,
        )
        for employee_uuid in participant_uuids
        if employee_uuid in employee_by_id
    ]

    return MeetingRecommendationResponse(
        start_dt=recommendation.start_dt,
        end_dt=recommendation.end_dt,
        required_available_ids=required_available,
        required_missing_ids=required_missing,
        optional_available_ids=optional_available,
        optional_missing_ids=optional_missing,
        overloaded_employee_ids=overloaded,
        local_times=local_times,
        available_employee_ids=required_available + optional_available,
        unavailable_employee_ids=required_missing + optional_missing + overloaded,
        score=recommendation.score,
    )


def _build_local_time(
    employee: Employee,
    start_dt: datetime,
    end_dt: datetime,
) -> EmployeeLocalTimeResponse:
    tz = ZoneInfo(employee.timezone)
    return EmployeeLocalTimeResponse(
        employee_id=employee.id,
        timezone=employee.timezone,
        local_start=start_dt.astimezone(tz),
        local_end=end_dt.astimezone(tz),
    )
