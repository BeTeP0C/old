from datetime import date, datetime, time
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.activity_event import ActivityEvent
from app.repositories.employee_metrics import EmployeeMetricRepository
from app.repositories.employees import EmployeeRepository
from app.repositories.team_members import TeamMemberRepository
from app.repositories.teams import TeamRepository
from app.repositories.work_schedules import WorkScheduleRepository
from app.services.exceptions import NotFoundError
from app.services.recommendations import RecommendationService


class AiContextRetriever:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.employees = EmployeeRepository(session)
        self.teams = TeamRepository(session)
        self.team_members = TeamMemberRepository(session)
        self.metrics = EmployeeMetricRepository(session)
        self.schedules = WorkScheduleRepository(session)
        self.recommendations = RecommendationService(session)

    async def get_employee_context(self, employee_id: UUID) -> dict[str, Any]:
        employee = await self.employees.get(employee_id)
        if employee is None:
            raise NotFoundError("employee not found")
        metric = await self.metrics.get_for_employee(employee_id)
        schedule = await self.schedules.get_active_for_employee(employee_id)
        events = await self.get_recent_employee_events(employee_id)
        recommendations = await self.recommendations.list_for_employee(employee_id)
        return {
            "employee": _model_dict(
                employee,
                ("id", "role", "full_name", "position", "timezone", "work_format"),
            ),
            "active_schedule": _model_dict(
                schedule,
                ("id", "work_days", "start_time", "end_time", "timezone", "last_updated_at"),
            ),
            "employee_metrics": _model_dict(
                metric,
                (
                    "id",
                    "calculated_at",
                    "days_since_update",
                    "actuality_score",
                    "outside_events_count",
                    "total_events_count",
                    "conflict_rate",
                    "load_level",
                    "risk_score",
                    "risk_level",
                ),
            ),
            "recent_activity_events": [
                _model_dict(
                    event,
                    (
                        "id",
                        "source",
                        "event_type",
                        "title",
                        "start_dt",
                        "end_dt",
                        "timezone",
                        "is_outside_schedule",
                    ),
                )
                for event in events
            ],
            "rule_based_recommendations": [
                recommendation.model_dump(mode="json") for recommendation in recommendations
            ],
        }

    async def get_team_context(self, team_id: UUID) -> dict[str, Any]:
        team = await self.teams.get(team_id)
        if team is None:
            raise NotFoundError("team not found")

        employee_ids = await self.team_members.list_employee_ids_for_team(team_id)
        employees = await self.employees.list_by_ids(employee_ids)
        metrics_by_employee = {
            metric.employee_id: metric
            for metric in await self.metrics.list_for_employees(employee_ids)
        }
        schedules_by_employee = {
            schedule.employee_id: schedule
            for schedule in await self.schedules.list_active_for_employees(employee_ids)
        }
        recommendations = await self.recommendations.list_for_team(team_id)
        return {
            "team": _model_dict(team, ("id", "name", "description")),
            "members": [
                {
                    "employee": _model_dict(
                        employee,
                        ("id", "role", "full_name", "position", "timezone", "work_format"),
                    ),
                    "employee_metrics": _model_dict(
                        metrics_by_employee.get(employee.id),
                        (
                            "id",
                            "calculated_at",
                            "actuality_score",
                            "conflict_rate",
                            "load_level",
                            "risk_score",
                            "risk_level",
                        ),
                    ),
                    "active_schedule": _model_dict(
                        schedules_by_employee.get(employee.id),
                        ("id", "work_days", "start_time", "end_time", "timezone"),
                    ),
                }
                for employee in employees
            ],
            "rule_based_recommendations": [
                recommendation.model_dump(mode="json") for recommendation in recommendations
            ],
        }

    async def get_recent_employee_events(
        self,
        employee_id: UUID,
        limit: int = 20,
    ) -> list[ActivityEvent]:
        result = await self.session.execute(
            select(ActivityEvent)
            .where(ActivityEvent.employee_id == employee_id)
            .order_by(ActivityEvent.start_dt.desc())
            .limit(limit)
        )
        return list(result.scalars().all())


def _model_dict(model: object | None, fields: tuple[str, ...]) -> dict[str, Any] | None:
    if model is None:
        return None
    return {field: _json_value(getattr(model, field)) for field in fields}


def _json_value(value: object) -> object:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime | date | time):
        return value.isoformat()
    return value
