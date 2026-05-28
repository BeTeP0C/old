"""Интеграционный тест end-to-end smart-уведомлений.

Сценарий: создаём сотрудника, прокидываем график «обновлён 90 дней назад»,
гоняем пересчёт метрик и проверяем, что в БД появилось ровно одно
уведомление типа `schedule_outdated` для этого сотрудника (а повторный
recompute в тот же день не создаёт второе — работает dedup).
"""

from datetime import UTC, datetime, time, timedelta
from uuid import UUID, uuid4

import psycopg
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.main import app
from app.models.notification import (
    NOTIFICATION_TYPE_SCHEDULE_OUTDATED,
    Notification,
)
from app.services.metric_calculator import MetricCalculatorService


def _sync_database_url() -> str:
    return settings.sqlalchemy_database_url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture(scope="module", autouse=True)
def require_database() -> None:
    try:
        with psycopg.connect(_sync_database_url(), connect_timeout=2) as connection:
            connection.execute("select 1")
    except psycopg.OperationalError as exc:
        pytest.skip(f"PostgreSQL is not available: {exc}")


@pytest.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as test_client:
        yield test_client


async def _create_employee(client: AsyncClient) -> dict[str, object]:
    suffix = uuid4().hex
    response = await client.post(
        "/api/v1/employees",
        json={
            "role": "employee",
            "full_name": f"Smart Test {suffix[:6]}",
            "email": f"smart-{suffix}@example.com",
            "timezone": "Europe/Moscow",
            "work_format": "office",
        },
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


async def _create_old_schedule(client: AsyncClient, employee_id: str) -> None:
    # last_updated_at = 90 дней назад → days_since_update заведомо ≥ 60.
    ninety_days_ago = datetime.now(UTC) - timedelta(days=90)
    response = await client.post(
        f"/api/v1/employees/{employee_id}/schedules",
        json={
            "employee_id": employee_id,
            "work_days": [0, 1, 2, 3, 4],
            "start_time": time(9, 0).isoformat(),
            "end_time": time(18, 0).isoformat(),
            "timezone": "Europe/Moscow",
            "work_format": "office",
            "last_updated_at": ninety_days_ago.isoformat(),
            "is_active": True,
        },
    )
    assert response.status_code == 201, response.text


async def _count_notifications(recipient_id: UUID, type_: str) -> int:
    async with AsyncSessionLocal() as s:
        result = await s.execute(
            select(Notification).where(
                Notification.recipient_id == recipient_id,
                Notification.type == type_,
            )
        )
        return len(list(result.scalars().all()))


@pytest.mark.asyncio
async def test_outdated_schedule_emits_notification_on_recompute(
    client: AsyncClient,
) -> None:
    employee = await _create_employee(client)
    employee_id = str(employee["id"])
    await _create_old_schedule(client, employee_id)

    # Гоняем пересчёт метрик напрямую через сервис.
    async with AsyncSessionLocal() as s:
        await MetricCalculatorService(s).recompute_for_employee_id(UUID(employee_id))

    count = await _count_notifications(
        UUID(employee_id), NOTIFICATION_TYPE_SCHEDULE_OUTDATED
    )
    assert count == 1, "outdated schedule should produce exactly one notification"


@pytest.mark.asyncio
async def test_recompute_twice_in_same_week_dedupes(client: AsyncClient) -> None:
    employee = await _create_employee(client)
    employee_id = str(employee["id"])
    await _create_old_schedule(client, employee_id)

    async with AsyncSessionLocal() as s:
        await MetricCalculatorService(s).recompute_for_employee_id(UUID(employee_id))
    async with AsyncSessionLocal() as s:
        await MetricCalculatorService(s).recompute_for_employee_id(UUID(employee_id))

    count = await _count_notifications(
        UUID(employee_id), NOTIFICATION_TYPE_SCHEDULE_OUTDATED
    )
    assert count == 1, "second recompute in same week must be deduped by uq index"


@pytest.mark.asyncio
async def test_notification_has_dedup_key_and_severity(client: AsyncClient) -> None:
    employee = await _create_employee(client)
    employee_id = str(employee["id"])
    await _create_old_schedule(client, employee_id)

    async with AsyncSessionLocal() as s:
        await MetricCalculatorService(s).recompute_for_employee_id(UUID(employee_id))

    async with AsyncSessionLocal() as s:
        result = await s.execute(
            select(Notification).where(
                Notification.recipient_id == UUID(employee_id),
                Notification.type == NOTIFICATION_TYPE_SCHEDULE_OUTDATED,
            )
        )
        notification = result.scalar_one()

    assert notification.dedup_key is not None
    assert notification.severity in {"low", "medium", "high", "critical"}
    assert notification.status in {"delivered", "deferred"}
    assert notification.payload is not None
    assert notification.payload.get("days_since_update", 0) >= 60
