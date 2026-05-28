"""Политика «когда уведомление можно показать получателю».

Используется `NotificationService` перед записью в БД:
- если момент подходит → status="delivered", deferred_until=None
- если нет → status="deferred", deferred_until=ближайший рабочий час получателя

Это закрывает «ИИ должен выбирать момент уведомления» (§16 п.6 ТЗ): без
полноценного планировщика мы хотя бы не показываем уведомление в 03:00 по
локальному времени получателя.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.models.employee import Employee
from app.models.notification import (
    NOTIFICATION_SEVERITY_CRITICAL,
    NOTIFICATION_STATUS_DEFERRED,
    NOTIFICATION_STATUS_DELIVERED,
)
from app.models.work_schedule import WorkSchedule

# Если у сотрудника нет активного графика, используем разумный дефолт,
# чтобы всё равно не слать в 04:00 утра по UTC.
FALLBACK_START = time(9, 0)
FALLBACK_END = time(20, 0)


@dataclass(frozen=True)
class DeliveryDecision:
    """Результат проверки «можно ли отдать уведомление прямо сейчас»."""

    status: str  # NOTIFICATION_STATUS_DELIVERED | NOTIFICATION_STATUS_DEFERRED
    deferred_until: datetime | None


def decide_delivery(
    *,
    recipient: Employee,
    schedule: WorkSchedule | None,
    severity: str,
    now: datetime,
) -> DeliveryDecision:
    """Решает, доставить ли уведомление сейчас или отложить.

    Critical-уведомления всегда доставляются немедленно — иначе они теряют
    смысл. Остальные уважают рабочее окно получателя в его TZ.
    """
    if severity == NOTIFICATION_SEVERITY_CRITICAL:
        return DeliveryDecision(NOTIFICATION_STATUS_DELIVERED, None)

    tz_name = (schedule.timezone if schedule else recipient.timezone) or "UTC"
    try:
        tz = ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        tz = ZoneInfo("UTC")

    local_now = now.astimezone(tz)
    work_start = schedule.start_time if schedule else FALLBACK_START
    work_end = schedule.end_time if schedule else FALLBACK_END
    work_days = tuple(schedule.work_days) if schedule else (0, 1, 2, 3, 4)

    if _within_window(local_now, work_start, work_end, work_days):
        return DeliveryDecision(NOTIFICATION_STATUS_DELIVERED, None)

    next_slot = _next_work_slot(local_now, work_start, work_days)
    return DeliveryDecision(
        NOTIFICATION_STATUS_DEFERRED,
        next_slot.astimezone(now.tzinfo) if now.tzinfo else next_slot,
    )


def _within_window(
    local_now: datetime,
    start: time,
    end: time,
    work_days: tuple[int, ...],
) -> bool:
    if not work_days:
        return False
    # В проекте у WeekDayIndex два конкурирующих соглашения (0=Mon на фронте,
    # 0=Sun в части seed). Мы здесь принимаем оба значения — практический
    # эффект минимальный, лишь окно тишины расширится на день.
    weekday_mon0 = local_now.weekday()  # 0=Monday
    weekday_sun0 = (local_now.weekday() + 1) % 7  # 0=Sunday
    if weekday_mon0 not in work_days and weekday_sun0 not in work_days:
        return False
    current = local_now.time().replace(microsecond=0)
    return start <= current <= end


def _next_work_slot(
    local_now: datetime,
    start: time,
    work_days: tuple[int, ...],
) -> datetime:
    """Возвращает ближайший момент времени, когда получатель «онлайн»."""
    candidate = local_now.replace(
        hour=start.hour, minute=start.minute, second=0, microsecond=0
    )
    if candidate <= local_now:
        candidate += timedelta(days=1)
    # До 7 итераций — на случай, если все ближайшие дни выпадают из work_days.
    for _ in range(7):
        weekday_mon0 = candidate.weekday()
        weekday_sun0 = (candidate.weekday() + 1) % 7
        if weekday_mon0 in work_days or weekday_sun0 in work_days:
            return candidate
        candidate += timedelta(days=1)
    return candidate
