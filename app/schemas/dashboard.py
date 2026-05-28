from datetime import datetime

from pydantic import BaseModel


class DashboardSummaryResponse(BaseModel):
    total_employees: int
    total_teams: int
    employees_by_risk_level: dict[str, int]
    overloaded_employees_count: int
    outdated_schedules_count: int
    outside_schedule_events_count: int
    last_calculation_at: datetime | None
    actual_schedules_count: int
    vacations_this_month: int
    # Агрегаты для /metrics и HR-дашборда. Считаются по EmployeeMetric;
    # если метрик нет — отдаются нули.
    average_actuality_score: float = 0.0
    average_risk_score: float = 0.0
    conflicts_rate: float = 0.0
    team_size: int = 0
