from datetime import datetime

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.metrics import ACTUALITY_DECAY_DAYS
from app.models.employee import Employee
from app.models.employee_metric import EmployeeMetric
from app.models.schedule_exception import ScheduleException
from app.models.team import Team

OVERLOADED_LOAD_LEVEL_THRESHOLD = 1.0
VACATION_EXCEPTION_TYPES = ("vacation",)


class DashboardRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def count_employees(self) -> int:
        return await self._scalar_int(select(func.count(Employee.id)))

    async def count_teams(self) -> int:
        return await self._scalar_int(select(func.count(Team.id)))

    async def count_employees_by_risk_level(self) -> dict[str, int]:
        result = await self.session.execute(
            select(EmployeeMetric.risk_level, func.count(EmployeeMetric.employee_id)).group_by(
                EmployeeMetric.risk_level
            )
        )
        return {risk_level: count for risk_level, count in result.all()}

    async def count_overloaded_employees(self) -> int:
        return await self._scalar_int(
            select(func.count(EmployeeMetric.employee_id)).where(
                EmployeeMetric.load_level > OVERLOADED_LOAD_LEVEL_THRESHOLD
            )
        )

    async def count_outdated_schedules(self) -> int:
        return await self._scalar_int(
            select(func.count(EmployeeMetric.employee_id)).where(
                EmployeeMetric.days_since_update >= ACTUALITY_DECAY_DAYS
            )
        )

    async def sum_outside_schedule_events(self) -> int:
        return await self._scalar_int(
            select(func.coalesce(func.sum(EmployeeMetric.outside_events_count), 0))
        )

    async def avg_actuality_score(self) -> float:
        result = await self.session.execute(
            select(func.coalesce(func.avg(EmployeeMetric.actuality_score), 0.0))
        )
        return float(result.scalar_one())

    async def avg_risk_score(self) -> float:
        result = await self.session.execute(
            select(func.coalesce(func.avg(EmployeeMetric.risk_score), 0.0))
        )
        return float(result.scalar_one())

    async def conflicts_rate(self) -> float:
        result = await self.session.execute(
            select(
                func.coalesce(func.sum(EmployeeMetric.outside_events_count), 0),
                func.coalesce(func.sum(EmployeeMetric.total_events_count), 0),
            )
        )
        outside, total = result.one()
        total_int = int(total)
        if total_int == 0:
            return 0.0
        return float(outside) / float(total_int)

    async def last_calculation_at(self) -> datetime | None:
        result = await self.session.execute(select(func.max(EmployeeMetric.calculated_at)))
        return result.scalar_one_or_none()

    async def count_vacations_in_range(self, start: datetime, end: datetime) -> int:
        return await self._scalar_int(
            select(func.count(ScheduleException.id)).where(
                ScheduleException.type.in_(VACATION_EXCEPTION_TYPES),
                ScheduleException.start_dt <= end,
                ScheduleException.end_dt >= start,
            )
        )

    async def _scalar_int(self, statement: Select[tuple[int]]) -> int:
        result = await self.session.execute(statement)
        return int(result.scalar_one())
