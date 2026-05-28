from datetime import UTC, datetime, time, timedelta
from uuid import UUID, uuid4

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
        pytest.skip(f"PostgreSQL is not available for metric calculator tests: {exc}")


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


def _insert_employee(
    connection: psycopg.Connection[tuple[object, ...]],
    *,
    timezone: str = "Europe/Moscow",
    work_format: str = "remote",
    updated_days_ago: int = 0,
) -> UUID:
    employee_id = uuid4()
    last_update = datetime.now(UTC) - timedelta(days=updated_days_ago)
    connection.execute(
        """
        insert into employees (
            id, role, full_name, email, timezone, work_format, updated_at
        )
        values (%s, %s, %s, %s, %s, %s, %s)
        """,
        (
            employee_id,
            "employee",
            f"Employee {uuid4().hex[:6]}",
            f"metric-{uuid4().hex}@example.com",
            timezone,
            work_format,
            last_update,
        ),
    )
    connection.execute(
        """
        insert into work_schedules (
            id, employee_id, work_days, start_time, end_time, timezone,
            work_format, last_updated_at, is_active
        )
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            uuid4(),
            employee_id,
            [0, 1, 2, 3, 4],
            time(9, 0),
            time(18, 0),
            timezone,
            "office",
            last_update,
            True,
        ),
    )
    return employee_id


def _insert_event(
    connection: psycopg.Connection[tuple[object, ...]],
    *,
    employee_id: UUID,
    start_dt: datetime,
    end_dt: datetime,
    timezone: str,
    source: str,
    is_outside_schedule: bool = False,
    recurrence_rule: str | None = None,
) -> None:
    connection.execute(
        """
        insert into activity_events (
            id, employee_id, source, event_type, title,
            start_dt, end_dt, timezone, recurrence_rule,
            is_recurring, is_outside_schedule
        )
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            uuid4(),
            employee_id,
            source,
            "meeting",
            "test event",
            start_dt,
            end_dt,
            timezone,
            recurrence_rule,
            recurrence_rule is not None,
            is_outside_schedule,
        ),
    )


@pytest.mark.asyncio
async def test_recompute_metrics_populates_zone_and_hr_factors(
    client: AsyncClient,
) -> None:
    today = datetime.now(UTC)
    with psycopg.connect(_sync_database_url()) as connection:
        employee_id = _insert_employee(connection, timezone="Europe/Moscow")
        for offset in range(4):
            start = today.replace(hour=10, minute=0, second=0, microsecond=0) - timedelta(
                days=offset + 1
            )
            _insert_event(
                connection=connection,
                employee_id=employee_id,
                start_dt=start,
                end_dt=start + timedelta(hours=1),
                timezone="Asia/Tokyo",
                source="calendar",
            )

    response = await client.post("/api/v1/admin/recompute-metrics?window_days=14")
    assert response.status_code == 200
    body = response.json()
    assert body["processed_count"] == 1
    assert body["window_days"] == 14

    with psycopg.connect(_sync_database_url()) as connection:
        row = connection.execute(
            "select zone_factor, hr_factor, total_events_count, risk_score, risk_level"
            " from employee_metrics where employee_id = %s",
            (employee_id,),
        ).fetchone()
    assert row is not None
    zone_factor_value, hr_factor_value, total_events, risk_score, risk_level = row
    assert total_events == 4
    assert zone_factor_value == 1.0  # все события в чужом часовом поясе
    assert hr_factor_value == 1.0  # все события из календаря, ни одного HR
    assert risk_score > 0.0
    assert risk_level in {"low", "medium", "high", "critical"}


@pytest.mark.asyncio
async def test_recompute_metrics_expands_recurring_event_in_window(
    client: AsyncClient,
) -> None:
    """Один RRULE-мастер в БД должен дать N occurrences в метриках (§18)."""
    now = datetime.now(UTC)
    window_days = 14
    with psycopg.connect(_sync_database_url()) as connection:
        employee_id = _insert_employee(connection, timezone="Europe/Moscow")
        # Ежедневная встреча 1ч в течение 10 дней, стартует 12 дней назад.
        master_start = (now - timedelta(days=12)).replace(
            hour=10, minute=0, second=0, microsecond=0
        )
        _insert_event(
            connection=connection,
            employee_id=employee_id,
            start_dt=master_start,
            end_dt=master_start + timedelta(hours=1),
            timezone="Europe/Moscow",
            source="calendar",
            recurrence_rule="FREQ=DAILY;COUNT=10",
        )

    response = await client.post(
        f"/api/v1/admin/recompute-metrics?window_days={window_days}"
    )
    assert response.status_code == 200

    with psycopg.connect(_sync_database_url()) as connection:
        row = connection.execute(
            "select total_events_count, load_level"
            " from employee_metrics where employee_id = %s",
            (employee_id,),
        ).fetchone()
    assert row is not None
    total_events, load = row
    # До фикса было бы 1 (один master-row). С expand_event должно быть ~10.
    assert total_events >= 8, (
        f"recurrence_rule не развернулся: total_events_count={total_events}"
    )
    # 10 occurrences по 1ч = 10ч busy. Load > 0.
    assert load > 0.0


@pytest.mark.asyncio
async def test_employees_category_in_absence_returns_only_employees_on_leave(
    client: AsyncClient,
) -> None:
    now = datetime.now(UTC)
    with psycopg.connect(_sync_database_url()) as connection:
        absent_id = _insert_employee(connection)
        active_id = _insert_employee(connection)
        connection.execute(
            """
            insert into schedule_exceptions (id, employee_id, type, start_dt, end_dt)
            values (%s, %s, %s, %s, %s)
            """,
            (
                uuid4(),
                absent_id,
                "vacation",
                now - timedelta(days=1),
                now + timedelta(days=1),
            ),
        )

    response = await client.get("/api/v1/employees?category=in_absence")
    assert response.status_code == 200
    payload = response.json()
    ids = {item["id"] for item in payload}
    assert str(absent_id) in ids
    assert str(active_id) not in ids


@pytest.mark.asyncio
async def test_team_availability_ranking_orders_by_overlap(
    client: AsyncClient,
) -> None:
    today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    with psycopg.connect(_sync_database_url()) as connection:
        team_a = uuid4()
        team_b = uuid4()
        connection.execute("insert into teams (id, name) values (%s, %s)", (team_a, "A-team"))
        connection.execute("insert into teams (id, name) values (%s, %s)", (team_b, "B-team"))
        for team_id in (team_a, team_b):
            employee_id = _insert_employee(connection)
            connection.execute(
                "insert into team_members (team_id, employee_id, role_in_team)"
                " values (%s, %s, %s)",
                (team_id, employee_id, "developer"),
            )

    response = await client.get("/api/v1/teams/availability-ranking?window_days=7")
    assert response.status_code == 200
    rows = response.json()
    assert len(rows) == 2
    assert all("overlap_ratio" in row for row in rows)
    overlap_ratios = [row["overlap_ratio"] for row in rows]
    assert overlap_ratios == sorted(overlap_ratios)
    assert rows[0]["total_window_minutes"] > 0
