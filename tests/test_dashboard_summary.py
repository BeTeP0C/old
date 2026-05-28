from datetime import UTC, datetime
from uuid import uuid4

import psycopg
import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import settings
from app.main import app


def _sync_database_url() -> str:
    return settings.sqlalchemy_database_url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture(scope="module", autouse=True)
def require_database() -> None:
    try:
        with psycopg.connect(_sync_database_url(), connect_timeout=2) as connection:
            connection.execute("select 1")
    except psycopg.OperationalError as exc:
        pytest.skip(f"PostgreSQL is not available for dashboard endpoint tests: {exc}")


@pytest.fixture(autouse=True)
def clean_database() -> None:
    with psycopg.connect(_sync_database_url()) as connection:
        connection.execute(
            """
            truncate table
                activity_events,
                employee_metrics,
                schedule_exceptions,
                team_members,
                work_schedules,
                employees,
                teams
            restart identity cascade
            """
        )


@pytest.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as test_client:
        yield test_client


@pytest.mark.asyncio
async def test_dashboard_summary_empty_database(client: AsyncClient) -> None:
    response = await client.get("/api/v1/dashboard/summary")

    assert response.status_code == 200
    assert response.json() == {
        "total_employees": 0,
        "total_teams": 0,
        "employees_by_risk_level": {
            "low": 0,
            "medium": 0,
            "high": 0,
            "critical": 0,
        },
        "overloaded_employees_count": 0,
        "outdated_schedules_count": 0,
        "outside_schedule_events_count": 0,
        "last_calculation_at": None,
        "actual_schedules_count": 0,
        "vacations_this_month": 0,
        "average_actuality_score": 0.0,
        "average_risk_score": 0.0,
        "conflicts_rate": 0.0,
        "team_size": 0,
    }


@pytest.mark.asyncio
async def test_dashboard_summary_non_empty_database(client: AsyncClient) -> None:
    now = datetime(2026, 5, 24, 12, 0, tzinfo=UTC)
    employee_ids = [uuid4(), uuid4(), uuid4()]
    team_ids = [uuid4(), uuid4()]

    with psycopg.connect(_sync_database_url()) as connection:
        for index, employee_id in enumerate(employee_ids):
            connection.execute(
                """
                insert into employees (
                    id, role, full_name, email, timezone, work_format
                )
                values (%s, %s, %s, %s, %s, %s)
                """,
                (
                    employee_id,
                    "employee",
                    f"Employee {index}",
                    f"employee-{index}-{uuid4().hex}@example.com",
                    "Europe/Moscow",
                    "remote",
                ),
            )
        for index, team_id in enumerate(team_ids):
            connection.execute(
                "insert into teams (id, name) values (%s, %s)",
                (team_id, f"Team {index}"),
            )

        metric_rows = [
            (employee_ids[0], 5, 0.94, 1, 5, 0.2, 0.7, 0.3, "low"),
            (employee_ids[1], 100, 0.0, 4, 8, 0.5, 1.2, 0.65, "high"),
        ]
        for row in metric_rows:
            connection.execute(
                """
                insert into employee_metrics (
                    id,
                    employee_id,
                    calculated_at,
                    days_since_update,
                    actuality_score,
                    outside_events_count,
                    total_events_count,
                    conflict_rate,
                    load_level,
                    risk_score,
                    risk_level
                )
                values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (uuid4(), row[0], now, *row[1:]),
            )

    response = await client.get("/api/v1/dashboard/summary")

    assert response.status_code == 200
    payload = response.json()
    # Считаем агрегаты отдельно через approx — иначе мелкие IEEE-расхождения дробей
    # ломают строгое сравнение.
    average_actuality_score = payload.pop("average_actuality_score")
    average_risk_score = payload.pop("average_risk_score")
    conflicts_rate = payload.pop("conflicts_rate")
    assert average_actuality_score == pytest.approx((0.94 + 0.0) / 2)
    assert average_risk_score == pytest.approx((0.3 + 0.65) / 2)
    assert conflicts_rate == pytest.approx((1 + 4) / (5 + 8))
    assert payload == {
        "total_employees": 3,
        "total_teams": 2,
        "employees_by_risk_level": {
            "low": 1,
            "medium": 0,
            "high": 1,
            "critical": 0,
        },
        "overloaded_employees_count": 1,
        "outdated_schedules_count": 1,
        "outside_schedule_events_count": 5,
        "last_calculation_at": "2026-05-24T12:00:00Z",
        "actual_schedules_count": 2,
        "vacations_this_month": 0,
        "team_size": 3,
    }
