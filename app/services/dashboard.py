from calendar import monthrange
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.dashboard import DashboardRepository
from app.schemas.dashboard import DashboardSummaryResponse

RISK_LEVELS = ("low", "medium", "high", "critical")


class DashboardService:
    def __init__(self, session: AsyncSession) -> None:
        self.dashboard = DashboardRepository(session)

    async def get_summary(self) -> DashboardSummaryResponse:
        employees_by_risk_level = dict.fromkeys(RISK_LEVELS, 0)
        employees_by_risk_level.update(await self.dashboard.count_employees_by_risk_level())

        total_employees = await self.dashboard.count_employees()
        outdated_schedules_count = await self.dashboard.count_outdated_schedules()
        actual_schedules_count = max(total_employees - outdated_schedules_count, 0)

        now = datetime.now(timezone.utc)
        start_of_month = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
        last_day = monthrange(now.year, now.month)[1]
        end_of_month = datetime(now.year, now.month, last_day, 23, 59, 59, tzinfo=timezone.utc)

        return DashboardSummaryResponse(
            total_employees=total_employees,
            total_teams=await self.dashboard.count_teams(),
            employees_by_risk_level=employees_by_risk_level,
            overloaded_employees_count=await self.dashboard.count_overloaded_employees(),
            outdated_schedules_count=outdated_schedules_count,
            outside_schedule_events_count=await self.dashboard.sum_outside_schedule_events(),
            last_calculation_at=await self.dashboard.last_calculation_at(),
            actual_schedules_count=actual_schedules_count,
            vacations_this_month=await self.dashboard.count_vacations_in_range(
                start_of_month, end_of_month
            ),
            average_actuality_score=await self.dashboard.avg_actuality_score(),
            average_risk_score=await self.dashboard.avg_risk_score(),
            conflicts_rate=await self.dashboard.conflicts_rate(),
            team_size=total_employees,
        )
