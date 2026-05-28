from datetime import UTC, datetime, timedelta
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
        pytest.skip(f"PostgreSQL is not available for activity event import tests: {exc}")


@pytest.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as test_client:
        yield test_client


async def _create_employee(client: AsyncClient) -> str:
    suffix = uuid4().hex
    response = await client.post(
        "/api/v1/employees",
        json={
            "vk_user_id": suffix[:12],
            "role": "employee",
            "full_name": "Grace Hopper",
            "email": f"grace-{suffix}@example.com",
            "timezone": "Europe/Moscow",
            "work_format": "office",
        },
    )
    assert response.status_code == 201
    return str(response.json()["id"])


@pytest.mark.asyncio
async def test_csv_import_and_employee_events_endpoint(client: AsyncClient) -> None:
    employee_id = await _create_employee(client)
    start = datetime.now(UTC)
    end = start + timedelta(hours=1)
    external_id = uuid4().hex
    csv_content = "\n".join(
        [
            "employee_id,external_id,source,event_type,title,start_dt,end_dt,timezone,is_recurring",
            (
                f"{employee_id},{external_id},mock,meeting,Planning,"
                f"{start.isoformat()},{end.isoformat()},Europe/Moscow,false"
            ),
        ]
    )

    response = await client.post(
        "/api/v1/import/events/csv",
        files={"file": ("events.csv", csv_content, "text/csv")},
    )

    assert response.status_code == 200
    assert response.json()["imported_count"] == 1

    events_response = await client.get(f"/api/v1/employees/{employee_id}/events")
    assert events_response.status_code == 200
    assert any(event["external_id"] == external_id for event in events_response.json())


@pytest.mark.asyncio
async def test_json_import_deduplicates_external_id(client: AsyncClient) -> None:
    employee_id = await _create_employee(client)
    start = datetime.now(UTC)
    end = start + timedelta(minutes=30)
    external_id = uuid4().hex
    payload = [
        {
            "employee_id": employee_id,
            "external_id": external_id,
            "source": "json",
            "event_type": "focus",
            "title": "Focus block",
            "start_dt": start.isoformat(),
            "end_dt": end.isoformat(),
            "timezone": "Europe/Moscow",
        }
    ]

    first_response = await client.post("/api/v1/import/events/json", json=payload)
    second_response = await client.post("/api/v1/import/events/json", json=payload)

    assert first_response.status_code == 200
    assert first_response.json()["imported_count"] == 1
    assert second_response.status_code == 200
    assert second_response.json()["imported_count"] == 0
    assert second_response.json()["skipped_duplicate_count"] == 1


@pytest.mark.asyncio
async def test_invalid_csv_row_returns_validation_errors(client: AsyncClient) -> None:
    employee_id = await _create_employee(client)
    start = datetime.now(UTC)
    end = start - timedelta(hours=1)
    csv_content = "\n".join(
        [
            "employee_id,source,event_type,title,start_dt,end_dt,timezone",
            (
                f"{employee_id},mock,meeting,Invalid,"
                f"{start.isoformat()},{end.isoformat()},Europe/Moscow"
            ),
        ]
    )

    response = await client.post(
        "/api/v1/import/events/csv",
        files={"file": ("events.csv", csv_content, "text/csv")},
    )

    assert response.status_code == 400
    assert "start_dt must be earlier than end_dt" in response.text


@pytest.mark.asyncio
async def test_csv_import_with_recurrence_rule_marks_event_recurring(
    client: AsyncClient,
) -> None:
    employee_id = await _create_employee(client)
    start = datetime.now(UTC)
    end = start + timedelta(hours=1)
    external_id = uuid4().hex
    csv_content = "\n".join(
        [
            "employee_id,external_id,source,event_type,title,"
            "start_dt,end_dt,timezone,is_recurring,recurrence_rule",
            (
                f"{employee_id},{external_id},mock,meeting,Weekly Sync,"
                f"{start.isoformat()},{end.isoformat()},Europe/Moscow,false,"
                # RRULE содержит запятую внутри BYDAY=MO,WE — оборачиваем в
                # CSV-кавычки, чтобы DictReader не разбил поле.
                '"FREQ=WEEKLY;BYDAY=MO,WE;COUNT=10"'
            ),
        ]
    )

    response = await client.post(
        "/api/v1/import/events/csv",
        files={"file": ("events.csv", csv_content, "text/csv")},
    )

    assert response.status_code == 200
    assert response.json()["imported_count"] == 1

    events_response = await client.get(f"/api/v1/employees/{employee_id}/events")
    assert events_response.status_code == 200
    imported = next(
        event for event in events_response.json() if event["external_id"] == external_id
    )
    assert imported["recurrence_rule"] == "FREQ=WEEKLY;BYDAY=MO,WE;COUNT=10"
    assert imported["is_recurring"] is True


@pytest.mark.asyncio
async def test_json_import_rejects_invalid_recurrence_rule(client: AsyncClient) -> None:
    employee_id = await _create_employee(client)
    start = datetime.now(UTC)
    end = start + timedelta(hours=1)
    payload = [
        {
            "employee_id": employee_id,
            "external_id": uuid4().hex,
            "source": "json",
            "event_type": "meeting",
            "title": "Bad rrule",
            "start_dt": start.isoformat(),
            "end_dt": end.isoformat(),
            "timezone": "Europe/Moscow",
            "recurrence_rule": "definitely-not-an-rrule",
        }
    ]

    response = await client.post("/api/v1/import/events/json", json=payload)

    assert response.status_code == 400
    assert "recurrence_rule" in response.text


@pytest.mark.asyncio
async def test_manual_event_create(client: AsyncClient) -> None:
    employee_id = await _create_employee(client)
    start = datetime.now(UTC)
    end = start + timedelta(minutes=15)

    response = await client.post(
        "/api/v1/events/manual",
        json={
            "employee_id": employee_id,
            "source": "manual",
            "event_type": "call",
            "title": "Quick sync",
            "start_dt": start.isoformat(),
            "end_dt": end.isoformat(),
            "timezone": "Europe/Moscow",
        },
    )

    assert response.status_code == 201
    assert response.json()["is_recurring"] is False
    assert response.json()["is_outside_schedule"] is False


@pytest.mark.asyncio
async def test_csv_import_uses_source_query_param_when_column_missing(
    client: AsyncClient,
) -> None:
    """Если CSV без колонки source, query-параметр заполняет её на всех строках."""
    employee_id = await _create_employee(client)
    start = datetime.now(UTC)
    end = start + timedelta(hours=1)
    external_id = uuid4().hex
    csv_content = "\n".join(
        [
            "employee_id,external_id,event_type,title,start_dt,end_dt,timezone",
            (
                f"{employee_id},{external_id},meeting,Standup,"
                f"{start.isoformat()},{end.isoformat()},Europe/Moscow"
            ),
        ]
    )

    response = await client.post(
        "/api/v1/import/events/csv?source=calendar",
        files={"file": ("events.csv", csv_content, "text/csv")},
    )
    assert response.status_code == 200, response.text
    assert response.json()["imported_count"] == 1

    events_response = await client.get(f"/api/v1/employees/{employee_id}/events")
    assert events_response.status_code == 200
    imported = next(
        event for event in events_response.json() if event["external_id"] == external_id
    )
    assert imported["source"] == "calendar"


@pytest.mark.asyncio
async def test_json_import_per_row_source_overrides_query_default(
    client: AsyncClient,
) -> None:
    """Явный source у строки имеет приоритет над query-параметром."""
    employee_id = await _create_employee(client)
    start = datetime.now(UTC)
    end = start + timedelta(minutes=45)
    explicit_id = uuid4().hex
    defaulted_id = uuid4().hex
    payload = [
        {
            "employee_id": employee_id,
            "external_id": explicit_id,
            "source": "tracker",
            "event_type": "task",
            "title": "Explicit source",
            "start_dt": start.isoformat(),
            "end_dt": end.isoformat(),
            "timezone": "Europe/Moscow",
        },
        {
            "employee_id": employee_id,
            "external_id": defaulted_id,
            "event_type": "meeting",
            "title": "No source field",
            "start_dt": start.isoformat(),
            "end_dt": end.isoformat(),
            "timezone": "Europe/Moscow",
        },
    ]

    response = await client.post(
        "/api/v1/import/events/json?source=hr",
        json=payload,
    )
    assert response.status_code == 200, response.text
    assert response.json()["imported_count"] == 2

    events_response = await client.get(f"/api/v1/employees/{employee_id}/events")
    events_by_external = {
        event["external_id"]: event for event in events_response.json()
    }
    assert events_by_external[explicit_id]["source"] == "tracker"
    assert events_by_external[defaulted_id]["source"] == "hr"


@pytest.mark.asyncio
async def test_csv_import_without_source_and_without_query_fails(
    client: AsyncClient,
) -> None:
    """Если source не задан ни в колонке, ни в query — старая ошибка валидации."""
    employee_id = await _create_employee(client)
    start = datetime.now(UTC)
    end = start + timedelta(hours=1)
    csv_content = "\n".join(
        [
            "employee_id,event_type,title,start_dt,end_dt,timezone",
            (
                f"{employee_id},meeting,Standup,"
                f"{start.isoformat()},{end.isoformat()},Europe/Moscow"
            ),
        ]
    )

    response = await client.post(
        "/api/v1/import/events/csv",
        files={"file": ("events.csv", csv_content, "text/csv")},
    )
    assert response.status_code == 400
    assert "source" in response.text
