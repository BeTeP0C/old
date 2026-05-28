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

    async def get_overview_context(self, top_n: int = 5) -> dict[str, Any]:
        """Срез по всем сотрудникам для general-вопросов HR (§16 п.1, п.3).

        Возвращает агрегаты + топы по нагрузке / устаревшим графикам /
        конфликтности — чтобы LLM мог отвечать на вопросы вида
        «кто перегружен?» / «у кого устарел график?» без явного employee_id.
        """
        employees = await self.employees.list()
        if not employees:
            return {"question_scope": "general", "employees_total": 0}

        employee_by_id = {employee.id: employee for employee in employees}
        metrics = await self.metrics.list_for_employees(list(employee_by_id))

        def _row(metric: object) -> dict[str, Any]:
            employee = employee_by_id.get(metric.employee_id)  # type: ignore[attr-defined]
            return {
                "employee_id": str(metric.employee_id),  # type: ignore[attr-defined]
                "full_name": employee.full_name if employee else None,
                "position": employee.position if employee else None,
                "actuality_score": metric.actuality_score,  # type: ignore[attr-defined]
                "load_level": metric.load_level,  # type: ignore[attr-defined]
                "conflict_rate": metric.conflict_rate,  # type: ignore[attr-defined]
                "days_since_update": metric.days_since_update,  # type: ignore[attr-defined]
                "risk_level": metric.risk_level,  # type: ignore[attr-defined]
                "risk_score": metric.risk_score,  # type: ignore[attr-defined]
            }

        rows = [_row(metric) for metric in metrics]

        risk_breakdown = {"low": 0, "medium": 0, "high": 0, "critical": 0}
        for row in rows:
            level = row["risk_level"]
            if level in risk_breakdown:
                risk_breakdown[level] += 1

        overloaded = sorted(
            (row for row in rows if row["load_level"] > 0.8),
            key=lambda r: r["load_level"],
            reverse=True,
        )[:top_n]
        outdated = sorted(
            (row for row in rows if row["actuality_score"] < 0.7),
            key=lambda r: r["actuality_score"],
        )[:top_n]
        high_conflict = sorted(
            (row for row in rows if row["conflict_rate"] > 0.15),
            key=lambda r: r["conflict_rate"],
            reverse=True,
        )[:top_n]
        highest_risk = sorted(rows, key=lambda r: r["risk_score"], reverse=True)[:top_n]

        return {
            "question_scope": "general",
            "employees_total": len(employees),
            "employees_with_metrics": len(metrics),
            "risk_level_breakdown": risk_breakdown,
            "top_overloaded": overloaded,
            "top_outdated_schedules": outdated,
            "top_conflicts": high_conflict,
            "top_risk": highest_risk,
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
