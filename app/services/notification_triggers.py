"""Триггеры smart-уведомлений по результатам пересчёта метрик.

Сравниваем предыдущее значение `EmployeeMetric` с только что рассчитанным и,
если risk_level вырос или Ai критично упал, эмитим Draft в NotificationService.

Без этого триггера никакая «smart» политика не имеет смысла: §16 п.6 говорит,
что система сама выбирает момент уведомления, а не пользователь.
"""

from __future__ import annotations

from datetime import datetime

from app.models.employee import Employee
from app.models.employee_metric import EmployeeMetric
from app.models.notification import (
    NOTIFICATION_SEVERITY_CRITICAL,
    NOTIFICATION_SEVERITY_HIGH,
    NOTIFICATION_SEVERITY_MEDIUM,
    NOTIFICATION_TYPE_RISK_INCREASED,
    NOTIFICATION_TYPE_SCHEDULE_OUTDATED,
)
from app.models.roadmap_item import ROADMAP_SUBJECT_EMPLOYEE
from app.services.notifications import NotificationDraft, NotificationService

# Порядок имеет значение: чем правее, тем «хуже».
_RISK_RANK: dict[str, int] = {"low": 0, "medium": 1, "high": 2, "critical": 3}
_RISK_TO_SEVERITY: dict[str, str] = {
    "medium": NOTIFICATION_SEVERITY_MEDIUM,
    "high": NOTIFICATION_SEVERITY_HIGH,
    "critical": NOTIFICATION_SEVERITY_CRITICAL,
}

# Сотрудника просим подтвердить график, если он не обновлялся столько дней.
SCHEDULE_OUTDATED_DAYS = 60


def _bucket(now: datetime) -> str:
    return now.date().isoformat()


async def maybe_emit_for_metric(
    *,
    employee: Employee,
    previous: EmployeeMetric | None,
    current: EmployeeMetric,
    notifications: NotificationService,
    now: datetime,
) -> int:
    """Возвращает количество созданных уведомлений (без учёта дедупликации)."""
    emitted = 0

    # 1) Поднялся risk_level → уведомить.
    prev_rank = _RISK_RANK.get(previous.risk_level, -1) if previous else -1
    cur_rank = _RISK_RANK.get(current.risk_level, 0)
    if cur_rank > prev_rank and current.risk_level in _RISK_TO_SEVERITY:
        severity = _RISK_TO_SEVERITY[current.risk_level]
        draft = NotificationDraft(
            type=NOTIFICATION_TYPE_RISK_INCREASED,
            severity=severity,
            title=_risk_title(current.risk_level),
            body=_risk_body(employee, current, previous),
            subject_type=ROADMAP_SUBJECT_EMPLOYEE,
            subject_id=employee.id,
            dedup_bucket=_bucket(now),
            payload={
                "employee_id": str(employee.id),
                "risk_level": current.risk_level,
                "previous_risk_level": previous.risk_level if previous else None,
                "risk_score": round(current.risk_score, 3),
                "actuality_score": round(current.actuality_score, 3),
                "conflict_rate": round(current.conflict_rate, 3),
                "load_level": round(current.load_level, 3),
            },
        )
        created = await notifications.emit(draft, now=now)
        emitted += len(created)

    # 2) График сильно устарел → попросить подтверждение.
    if current.days_since_update >= SCHEDULE_OUTDATED_DAYS:
        draft = NotificationDraft(
            type=NOTIFICATION_TYPE_SCHEDULE_OUTDATED,
            severity=NOTIFICATION_SEVERITY_HIGH,
            title="Рабочий график устарел",
            body=(
                f"{employee.full_name} не обновлял рабочий график "
                f"{current.days_since_update} дн. Это влияет на планирование команды."
            ),
            subject_type=ROADMAP_SUBJECT_EMPLOYEE,
            subject_id=employee.id,
            # Bucket = неделя: чтобы не слать каждый день одно и то же.
            dedup_bucket=_week_bucket(now),
            payload={
                "employee_id": str(employee.id),
                "days_since_update": current.days_since_update,
            },
        )
        created = await notifications.emit(draft, now=now)
        emitted += len(created)

    return emitted


def _risk_title(level: str) -> str:
    label = {
        "medium": "Средний риск неактуальности",
        "high": "Высокий риск неактуальности",
        "critical": "Критический риск неактуальности",
    }.get(level, "Изменение риска неактуальности")
    return label


def _risk_body(
    employee: Employee,
    current: EmployeeMetric,
    previous: EmployeeMetric | None,
) -> str:
    if previous is not None:
        return (
            f"{employee.full_name}: уровень риска вырос {previous.risk_level} → "
            f"{current.risk_level}. Ai={current.actuality_score:.2f}, "
            f"Ci={current.conflict_rate:.0%}, Li={current.load_level:.0%}."
        )
    return (
        f"{employee.full_name}: риск {current.risk_level}. "
        f"Ai={current.actuality_score:.2f}, Ci={current.conflict_rate:.0%}, "
        f"Li={current.load_level:.0%}."
    )


def _week_bucket(now: datetime) -> str:
    iso_year, iso_week, _ = now.isocalendar()
    return f"{iso_year}-W{iso_week:02d}"
