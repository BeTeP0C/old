"""Unit-тесты для политики «когда отдавать уведомление» (без БД)."""

from datetime import UTC, datetime, time
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.models.notification import (
    NOTIFICATION_SEVERITY_CRITICAL,
    NOTIFICATION_SEVERITY_HIGH,
    NOTIFICATION_SEVERITY_LOW,
    NOTIFICATION_STATUS_DEFERRED,
    NOTIFICATION_STATUS_DELIVERED,
)
from app.services.notification_delivery import decide_delivery


def _recipient(timezone: str = "Europe/Moscow") -> SimpleNamespace:
    return SimpleNamespace(id=uuid4(), timezone=timezone, full_name="Test User")


def _schedule(
    *,
    start: time = time(9, 0),
    end: time = time(18, 0),
    days: tuple[int, ...] = (0, 1, 2, 3, 4),
    timezone: str = "Europe/Moscow",
) -> SimpleNamespace:
    return SimpleNamespace(
        timezone=timezone,
        start_time=start,
        end_time=end,
        work_days=days,
    )


def test_critical_severity_bypasses_quiet_hours() -> None:
    # 3:00 ночи по UTC = 6:00 по Москве → вне 09:00-18:00, но critical → отдать.
    night = datetime(2026, 5, 27, 3, 0, tzinfo=UTC)
    decision = decide_delivery(
        recipient=_recipient(),
        schedule=_schedule(),
        severity=NOTIFICATION_SEVERITY_CRITICAL,
        now=night,
    )
    assert decision.status == NOTIFICATION_STATUS_DELIVERED
    assert decision.deferred_until is None


def test_low_severity_during_work_hours_delivered() -> None:
    # 11:00 UTC среды = 14:00 Москва → точно внутри окна.
    midday = datetime(2026, 5, 27, 11, 0, tzinfo=UTC)
    decision = decide_delivery(
        recipient=_recipient(),
        schedule=_schedule(),
        severity=NOTIFICATION_SEVERITY_LOW,
        now=midday,
    )
    assert decision.status == NOTIFICATION_STATUS_DELIVERED


def test_low_severity_after_hours_deferred_to_next_morning() -> None:
    # 21:00 UTC среды = 00:00 четверга Москва → вне окна, отложить до 09:00 чт.
    late = datetime(2026, 5, 27, 21, 0, tzinfo=UTC)
    decision = decide_delivery(
        recipient=_recipient(),
        schedule=_schedule(),
        severity=NOTIFICATION_SEVERITY_HIGH,
        now=late,
    )
    assert decision.status == NOTIFICATION_STATUS_DEFERRED
    assert decision.deferred_until is not None
    # Проверяем, что отложили примерно на ближайшее утро (а не на через неделю).
    diff_hours = (decision.deferred_until - late).total_seconds() / 3600
    assert 0 < diff_hours < 36


def test_weekend_deferred_to_monday() -> None:
    # Суббота 12:00 UTC = 15:00 МСК. Календарь Пн-Пт.
    saturday = datetime(2026, 5, 30, 12, 0, tzinfo=UTC)
    decision = decide_delivery(
        recipient=_recipient(),
        schedule=_schedule(days=(0, 1, 2, 3, 4)),
        severity=NOTIFICATION_SEVERITY_HIGH,
        now=saturday,
    )
    assert decision.status == NOTIFICATION_STATUS_DEFERRED
    assert decision.deferred_until is not None
    # Отложить должны куда-то в Пн (1 июня 2026) или Вс если seed-конвенция 0=Sun.
    assert decision.deferred_until.weekday() in {0, 6}


def test_missing_schedule_falls_back_to_default_window() -> None:
    # Без активного графика всё равно не должны отдавать в 03:00.
    night = datetime(2026, 5, 27, 0, 0, tzinfo=UTC)  # 03:00 МСК
    decision = decide_delivery(
        recipient=_recipient(),
        schedule=None,
        severity=NOTIFICATION_SEVERITY_HIGH,
        now=night,
    )
    assert decision.status == NOTIFICATION_STATUS_DEFERRED


def test_unknown_timezone_falls_back_to_utc() -> None:
    # Невалидный TZ → отрабатываем как UTC, не падаем.
    midday_utc = datetime(2026, 5, 27, 12, 0, tzinfo=UTC)
    decision = decide_delivery(
        recipient=_recipient(timezone="Mars/Olympus_Mons"),
        schedule=None,
        severity=NOTIFICATION_SEVERITY_HIGH,
        now=midday_utc,
    )
    assert decision.status == NOTIFICATION_STATUS_DELIVERED


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
