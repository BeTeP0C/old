"""Заполняет БД реалистичными тестовыми данными.

Использование:
    ./venv/bin/python -m scripts.seed                     # 30 сотрудников + 5 команд (программно)
    ./venv/bin/python -m scripts.seed --reset             # то же с TRUNCATE
    ./venv/bin/python -m scripts.seed --reset --small     # 8 сотрудников + 2 команды
    ./venv/bin/python -m scripts.seed --reset --small --from-files
                                                          # small из CSV/JSON фикстур
    ./venv/bin/python -m scripts.seed --reset --no-roadmap
                                                          # не вызывать RoadmapService.generate()

Создаёт:
  - сотрудники + 1 admin (test@example.com / pass1234)
  - команды с участниками (lead, pm, members)
  - WorkSchedule с разным last_updated_at
  - ScheduleException у части сотрудников
  - ActivityEvent за последние 30 дней
  - EmployeeMetric с распределением risk_level: low/medium/high/critical
  - ScheduleConfirmationRequest для устаревших графиков
  - RoadmapItem (через RoadmapService.generate) если не указан --no-roadmap
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import random
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.recurrence import expand_event
from app.core.passwords import hash_password
from app.db.session import AsyncSessionLocal, engine
from app.models.activity_event import ActivityEvent
from app.models.employee import Employee
from app.models.employee_metric import EmployeeMetric
from app.models.employee_metric_snapshot import EmployeeMetricSnapshot
from app.models.schedule_confirmation_request import (
    CONFIRMATION_STATUS_PENDING,
    ScheduleConfirmationRequest,
)
from app.models.schedule_exception import ScheduleException
from app.models.team import Team
from app.models.team_member import TeamMember
from app.models.work_schedule import WorkSchedule
from app.services.roadmap import RoadmapGenerateRequest, RoadmapService

SEED_DATA_ROOT = Path(__file__).parent / "seed_data"
SMALL_EMPLOYEE_COUNT = 8
SMALL_TEAM_COUNT = 2

SEED = 42

ADMIN_EMAIL = "test@example.com"
ADMIN_PASSWORD = "pass1234"
ADMIN_FULL_NAME = "Тестовый Администратор"

# Пароль для не-админских демо-аккаунтов.
DEMO_PASSWORD = "demo1234"

# 5 демо-аккаунтов (по одному на роль) с паролями и полностью заполненными
# профилями — показываются на странице входа. Первый совпадает с ADMIN_*.
# (email, password, role, full_name, position, timezone, work_format)
DEMO_ACCOUNTS: list[tuple[str, str, str, str, str, str, str]] = [
    (ADMIN_EMAIL, ADMIN_PASSWORD, "admin", ADMIN_FULL_NAME, "Администратор", "Europe/Moscow", "hybrid"),
    ("manager@worktime.dev", DEMO_PASSWORD, "manager", "Игорь Соколов",
     "Руководитель отдела разработки", "Europe/Moscow", "office"),
    ("hr@worktime.dev", DEMO_PASSWORD, "hr", "Елена Кадрова",
     "HR-специалист", "Europe/Moscow", "hybrid"),
    ("pm@worktime.dev", DEMO_PASSWORD, "pm", "Павел Орлов",
     "Проектный менеджер", "Europe/Moscow", "remote"),
    ("employee@worktime.dev", DEMO_PASSWORD, "employee", "Семён Зайцев",
     "Frontend-разработчик", "Asia/Yekaterinburg", "remote"),
]

TIMEZONES = ["Europe/Moscow", "Europe/Kaliningrad", "Asia/Yekaterinburg", "Europe/London"]
WORK_FORMATS = ["office", "remote", "hybrid"]

EMPLOYEE_NAMES: list[tuple[str, str, str]] = [
    ("Михаил", "Петров", "Backend-разработчик"),
    ("Анна", "Сидорова", "Frontend-разработчик"),
    ("Иван", "Кузнецов", "Backend-разработчик"),
    ("Ольга", "Смирнова", "Дизайнер"),
    ("Дмитрий", "Морозов", "DevOps"),
    ("Екатерина", "Волкова", "QA-инженер"),
    ("Алексей", "Зайцев", "Project Manager"),
    ("Мария", "Соколова", "Frontend-разработчик"),
    ("Сергей", "Лебедев", "Backend-разработчик"),
    ("Татьяна", "Козлова", "Бизнес-аналитик"),
    ("Андрей", "Новиков", "Системный аналитик"),
    ("Наталья", "Воробьёва", "HR"),
    ("Владимир", "Орлов", "Тимлид backend"),
    ("Юлия", "Беляева", "Frontend-разработчик"),
    ("Павел", "Соловьёв", "Mobile-разработчик iOS"),
    ("Ирина", "Васильева", "Mobile-разработчик Android"),
    ("Николай", "Григорьев", "Архитектор"),
    ("Светлана", "Романова", "UX-дизайнер"),
    ("Виктор", "Захаров", "Data-инженер"),
    ("Елена", "Степанова", "ML-инженер"),
    ("Артём", "Никитин", "Backend-разработчик"),
    ("Полина", "Макарова", "Product Manager"),
    ("Денис", "Жуков", "QA-инженер"),
    ("Алина", "Фролова", "QA-инженер"),
    ("Роман", "Гусев", "DevOps"),
    ("Дарья", "Киселёва", "Контент-менеджер"),
    ("Григорий", "Ильин", "Тимлид frontend"),
    ("Кристина", "Карпова", "Дизайнер"),
    ("Антон", "Тихонов", "Backend-разработчик"),
    ("Валентина", "Сорокина", "Маркетолог"),
]

TEAM_DEFS: list[tuple[str, str]] = [
    ("Команда разработки", "Backend, Frontend, DevOps и QA — основной продукт."),
    ("Мобильная разработка", "iOS, Android и кроссплатформенные решения."),
    ("Data & ML", "Платформа данных, аналитика и ML-фичи."),
    ("Продукт и аналитика", "Product-менеджеры, бизнес- и системные аналитики."),
    ("Дизайн и контент", "UX/UI, графика, контент-маркетинг."),
]

EVENT_TITLES_BY_TYPE: dict[str, list[str]] = {
    "meeting": [
        "Daily standup",
        "Planning",
        "Retrospective",
        "Grooming",
        "Sync с заказчиком",
        "Демо",
        "1:1 с руководителем",
        "Архитектурный комитет",
        "Кросс-командный sync",
    ],
    "focus": [
        "Focus block",
        "Глубокая работа: рефакторинг",
        "Code review",
        "Разбор инцидента",
        "Подготовка к релизу",
        "Документация",
    ],
    "call": [
        "Звонок с клиентом",
        "Интервью кандидата",
        "Звонок с подрядчиком",
        "Demo для стейкхолдеров",
    ],
}

EXCEPTION_TYPES = ["vacation", "sick", "sick_leave"]
EXCEPTION_REASONS = {
    "vacation": ["Запланированный отпуск", "Отпуск", "Семейные обстоятельства"],
    "sick": ["ОРВИ", "Больничный", "Восстановление"],
    "sick_leave": ["Больничный лист", "Лечение"],
}


def now_utc() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


async def reset_tables(session: AsyncSession) -> None:
    """Чистим все доменные таблицы. alembic_version не трогаем.

    CASCADE сам разрулит FK; перечисляем все таблицы явно, чтобы
    забытая таблица не оставила «висячих» данных между прогонами сида.
    """
    tables = [
        "notifications",
        "roadmap_items",
        "change_history",
        "import_logs",
        "activity_events",
        "schedule_confirmation_requests",
        "schedule_exceptions",
        "work_schedules",
        "employee_metric_snapshots",
        "employee_metrics",
        "refresh_tokens",
        "ai_chunks",
        "ai_documents",
        "team_members",
        "teams",
        "employees",
    ]
    await session.execute(text(f"TRUNCATE {', '.join(tables)} RESTART IDENTITY CASCADE"))
    await session.commit()
    print(f"  очищено таблиц: {len(tables)}")


def make_employees(rng: random.Random, *, limit: int | None = None) -> list[Employee]:
    employees: list[Employee] = []
    # Демо-аккаунты (по роли, с паролями). Первый — админ; роль admin
    # обязательна, иначе POST /employees/full и прочие admin/hr-only эндпоинты
    # отдают 403 под этим логином.
    for email, password, role, full_name, position, tz, work_format in DEMO_ACCOUNTS:
        employees.append(
            Employee(
                email=email,
                password_hash=hash_password(password),
                full_name=full_name,
                role=role,
                position=position,
                timezone=tz,
                work_format=work_format,
            )
        )
    names = EMPLOYEE_NAMES if limit is None else EMPLOYEE_NAMES[:limit]
    for first, last, position in names:
        is_head = "Тимлид" in position or position in {"Project Manager", "Product Manager"}
        is_hr = position == "HR"
        if is_head:
            role = "pm" if "Manager" in position else "manager"
        elif is_hr:
            role = "hr"
        elif "аналитик" in position.lower():
            role = "analyst"
        else:
            role = "employee"
        email = f"{_translit(first)}.{_translit(last)}@worktime.example".lower()
        employees.append(
            Employee(
                email=email,
                full_name=f"{first} {last}",
                role=role,
                position=position,
                timezone=rng.choice(TIMEZONES),
                work_format=rng.choice(WORK_FORMATS),
            )
        )
    return employees


def make_teams(*, limit: int | None = None) -> list[Team]:
    defs = TEAM_DEFS if limit is None else TEAM_DEFS[:limit]
    return [Team(name=name, description=desc) for name, desc in defs]


def make_team_members(
    rng: random.Random,
    teams: list[Team],
    employees: list[Employee],
) -> list[TeamMember]:
    """Каждый сотрудник попадает в 1–2 команды; в каждой команде есть lead, опционально pm."""
    members: list[TeamMember] = []
    # каждой команде назначим lead и pm из первых подходящих сотрудников
    leads_assigned: set[UUID] = set()
    pms_assigned: set[UUID] = set()

    # сначала пробросим employees из списка с подходящей ролью как lead/pm
    by_role: dict[str, list[Employee]] = {"manager": [], "pm": [], "employee": []}
    for emp in employees:
        if emp.role in by_role:
            by_role[emp.role].append(emp)

    rng.shuffle(by_role["manager"])
    rng.shuffle(by_role["pm"])

    for team in teams:
        # lead = head если есть, иначе случайный employee
        lead_pool = [e for e in by_role["manager"] if e.id not in leads_assigned] or [
            e for e in employees if e.id not in leads_assigned and e.email != ADMIN_EMAIL
        ]
        lead = lead_pool[0]
        leads_assigned.add(lead.id)
        members.append(TeamMember(team=team, employee=lead, role_in_team="lead"))

        # pm
        pm_pool = [e for e in by_role["pm"] if e.id not in pms_assigned and e.id != lead.id]
        if pm_pool:
            pm = pm_pool[0]
            pms_assigned.add(pm.id)
            members.append(TeamMember(team=team, employee=pm, role_in_team="pm"))

    # остальные — членами команд (включая админа/демо — чтобы профиль был с командой)
    leaders_and_pms = leads_assigned | pms_assigned
    pool = [e for e in employees if e.id not in leaders_and_pms]
    rng.shuffle(pool)

    # каждому 1–2 команды; держим целевое распределение ~5-8 на команду
    target_per_team = {team.id: rng.randint(5, 8) for team in teams}
    counts: dict[UUID, int] = {team.id: 1 + (1 if team.id in pms_assigned else 0) for team in teams}
    # уточнение counts: учтём lead+pm
    counts = {}
    for team in teams:
        counts[team.id] = sum(1 for m in members if m.team is team)

    for emp in pool:
        # выбираем 1–2 команды с наименьшим заполнением
        teams_sorted = sorted(teams, key=lambda t: counts[t.id])
        n_teams = 1 if rng.random() < 0.7 else 2
        emp_teams: list[Team] = []
        for team in teams_sorted:
            if counts[team.id] >= target_per_team[team.id]:
                continue
            emp_teams.append(team)
            if len(emp_teams) == n_teams:
                break
        if not emp_teams:
            emp_teams = [teams_sorted[0]]
        for team in emp_teams:
            members.append(TeamMember(team=team, employee=emp, role_in_team="member"))
            counts[team.id] += 1

    # admin → пусть будет lead везде? нет, не нужно. Просто пропустим.
    return members


def make_work_schedules(rng: random.Random, employees: list[Employee]) -> list[WorkSchedule]:
    schedules: list[WorkSchedule] = []
    now = now_utc()
    for i, emp in enumerate(employees):
        # work_days [1-5] = пн-пт (в нашем коде 0=воскресенье; используем стандартное Mon-Fri как 1-5)
        # некоторым сделаем "плавающий" график
        work_days = [1, 2, 3, 4, 5] if rng.random() > 0.1 else [1, 2, 3, 4]
        start_hour = rng.choice([8, 9, 9, 9, 10, 10])
        duration = rng.choice([8, 9, 9, 9])
        # last_updated — от вчера до 100 дней назад, чтобы получить разные days_since_update
        days_back = _days_back_for_index(i, len(employees))
        last_updated = now - timedelta(days=days_back, hours=rng.randint(0, 12))
        # ~30% сотрудников уже подтвердили график — confirmed_at не старше 14 дней
        confirmed_at = None
        if rng.random() < 0.3:
            confirmed_at = now - timedelta(days=rng.randint(0, 14), hours=rng.randint(0, 12))
        schedules.append(
            WorkSchedule(
                employee_id=emp.id,
                work_days=work_days,
                start_time=time(hour=start_hour, minute=0),
                end_time=time(hour=start_hour + duration, minute=0),
                timezone=emp.timezone,
                work_format=emp.work_format,
                last_updated_at=last_updated,
                confirmed_at=confirmed_at,
                is_active=True,
            )
        )
    return schedules


def make_confirmation_requests(
    rng: random.Random,
    employees: list[Employee],
    schedules_by_emp: dict[UUID, WorkSchedule],
) -> list[ScheduleConfirmationRequest]:
    """Создаём pending запросы для сотрудников с устаревшим графиком.

    Берём ~5 кандидатов с наибольшим days_since_update и без confirmed_at.
    Если их меньше 5 — добираем случайно.
    """
    now = now_utc()
    requesters = [
        emp
        for emp in employees
        if emp.role in {"hr", "manager"} and emp.email != ADMIN_EMAIL
    ] or [emp for emp in employees if emp.email != ADMIN_EMAIL]
    candidates = sorted(
        (emp for emp in employees if emp.email != ADMIN_EMAIL),
        key=lambda e: (
            -(now - schedules_by_emp[e.id].last_updated_at).days
            if e.id in schedules_by_emp
            else 0
        ),
    )
    # Кандидат должен иметь активный график и не быть свежеподтверждённым.
    targets: list[Employee] = []
    for emp in candidates:
        schedule = schedules_by_emp.get(emp.id)
        if schedule is None:
            continue
        if schedule.confirmed_at is not None:
            continue
        targets.append(emp)
        if len(targets) >= 5:
            break

    reasons = [
        "График явно устарел: 60+ дней без обновлений.",
        "Часто встречи вне рабочих часов.",
        "Проверьте текущий формат работы.",
        "Расхождение с HR-данными.",
        None,
    ]
    requests: list[ScheduleConfirmationRequest] = []
    for emp in targets:
        requester = rng.choice(requesters)
        if requester.id == emp.id and len(requesters) > 1:
            requester = next(r for r in requesters if r.id != emp.id)
        requests.append(
            ScheduleConfirmationRequest(
                employee_id=emp.id,
                requested_by_id=requester.id,
                reason=rng.choice(reasons),
                status=CONFIRMATION_STATUS_PENDING,
                created_at=now - timedelta(days=rng.randint(0, 5)),
            )
        )
    return requests


def make_exceptions(rng: random.Random, employees: list[Employee]) -> list[ScheduleException]:
    exceptions: list[ScheduleException] = []
    now = now_utc()
    # ~30% сотрудников получают 1–2 исключения
    pool = [e for e in employees if e.email != ADMIN_EMAIL]
    target = max(1, int(len(pool) * 0.3))
    chosen = rng.sample(pool, target)
    for emp in chosen:
        count = rng.choice([1, 1, 2])
        for _ in range(count):
            exc_type = rng.choice(EXCEPTION_TYPES)
            length = rng.randint(1, 14)
            offset = rng.randint(-20, 40)  # часть в прошлом, часть в будущем
            start = (now + timedelta(days=offset)).replace(hour=0, minute=0, second=0)
            end = start + timedelta(days=length)
            exceptions.append(
                ScheduleException(
                    employee_id=emp.id,
                    type=exc_type,
                    start_dt=start,
                    end_dt=end,
                    reason=rng.choice(EXCEPTION_REASONS[exc_type]),
                )
            )
    return exceptions


def make_activity_events(
    rng: random.Random,
    employees: list[Employee],
    schedules_by_emp: dict[UUID, WorkSchedule],
) -> list[ActivityEvent]:
    """Генерируем ~30 событий на сотрудника за последние 30 дней."""
    events: list[ActivityEvent] = []
    now = now_utc()
    types_distribution = ["meeting"] * 5 + ["focus"] * 3 + ["call"] * 2

    for emp in employees:
        sched = schedules_by_emp.get(emp.id)
        count = rng.randint(20, 40)
        for i in range(count):
            event_type = rng.choice(types_distribution)
            title = rng.choice(EVENT_TITLES_BY_TYPE[event_type])
            day_offset = rng.randint(0, 30)
            # время старта в рамках рабочего дня (с шансом выйти за пределы)
            outside = rng.random() < 0.15
            if sched and not outside:
                hour = rng.randint(sched.start_time.hour, max(sched.start_time.hour, sched.end_time.hour - 1))
            else:
                hour = rng.choice([7, 8, 19, 20, 21])
            minute = rng.choice([0, 15, 30, 45])
            duration_minutes = rng.choice([30, 30, 45, 60, 60, 60, 90])
            start = (now - timedelta(days=day_offset)).replace(
                hour=hour, minute=minute, second=0, microsecond=0
            )
            end = start + timedelta(minutes=duration_minutes)
            recurrence_rule = _maybe_recurrence_rule(rng, event_type)
            events.append(
                ActivityEvent(
                    employee_id=emp.id,
                    external_id=f"seed-{emp.id}-{i}",
                    source="mock",
                    event_type=event_type,
                    title=title,
                    start_dt=start,
                    end_dt=end,
                    timezone=emp.timezone,
                    recurrence_rule=recurrence_rule,
                    is_recurring=recurrence_rule is not None,
                    is_outside_schedule=outside,
                )
            )
    return events


def _maybe_recurrence_rule(rng: random.Random, event_type: str) -> str | None:
    """~15% событий получают RRULE, чтобы метрики увидели разницу."""
    if rng.random() >= 0.15:
        return None
    if event_type == "focus":
        return "FREQ=DAILY;COUNT=10"
    return rng.choice(
        [
            "FREQ=WEEKLY;BYDAY=MO,WE,FR;COUNT=12",
            "FREQ=WEEKLY;BYDAY=TU,TH;COUNT=8",
            "FREQ=WEEKLY;COUNT=6",
        ]
    )


def make_metrics(
    rng: random.Random,
    employees: list[Employee],
    events_by_emp: dict[UUID, list[ActivityEvent]],
    schedules_by_emp: dict[UUID, WorkSchedule],
) -> list[EmployeeMetric]:
    """Распределяем risk_level: 35% low, 25% medium, 25% high, 15% critical."""
    metrics: list[EmployeeMetric] = []
    now = now_utc()
    # Включаем всех (в т.ч. админа/демо-аккаунты), чтобы их профиль был заполнен
    # — иначе «Мой Ai» показывает «Нет данных».
    eligible = list(employees)
    n = len(eligible)
    distribution = (
        ["low"] * int(n * 0.35)
        + ["medium"] * int(n * 0.25)
        + ["high"] * int(n * 0.25)
        + ["critical"] * int(n * 0.15)
    )
    # добиваем до n штук
    while len(distribution) < n:
        distribution.append("low")
    rng.shuffle(distribution)

    for emp, risk_level in zip(eligible, distribution, strict=False):
        sched = schedules_by_emp.get(emp.id)
        days_since = (
            (now - sched.last_updated_at).days if sched else rng.randint(1, 90)
        )
        emp_events = events_by_emp.get(emp.id, [])
        metrics_window_end = now
        metrics_window_start = now - timedelta(days=30)
        expanded_intervals = [
            interval
            for event in emp_events
            for interval in expand_event(event, metrics_window_start, metrics_window_end)
        ]
        total = len(expanded_intervals)
        outside = sum(1 for interval in expanded_intervals if interval.is_outside_schedule)

        risk_ranges = {
            "low": (0.05, 0.30),
            "medium": (0.30, 0.55),
            "high": (0.55, 0.78),
            "critical": (0.78, 0.95),
        }
        risk_score = round(rng.uniform(*risk_ranges[risk_level]), 2)

        actuality = round(max(0.0, min(1.0, 1.0 - days_since / 100.0 + rng.uniform(-0.1, 0.1))), 2)
        conflict_rate = round(
            {
                "low": rng.uniform(0.0, 0.15),
                "medium": rng.uniform(0.10, 0.30),
                "high": rng.uniform(0.25, 0.50),
                "critical": rng.uniform(0.40, 0.65),
            }[risk_level],
            2,
        )
        load_level = round(
            {
                "low": rng.uniform(0.30, 0.65),
                "medium": rng.uniform(0.55, 0.80),
                "high": rng.uniform(0.70, 0.95),
                "critical": rng.uniform(0.85, 1.10),
            }[risk_level],
            2,
        )
        metrics.append(
            EmployeeMetric(
                employee_id=emp.id,
                calculated_at=now - timedelta(hours=rng.randint(1, 24)),
                days_since_update=days_since,
                actuality_score=actuality,
                outside_events_count=outside,
                total_events_count=total,
                conflict_rate=conflict_rate,
                load_level=load_level,
                risk_score=risk_score,
                risk_level=risk_level,
            )
        )
    return metrics


def make_metric_snapshots(
    rng: random.Random,
    metrics: list[EmployeeMetric],
    *,
    points: int = 12,
    step_days: int = 14,
) -> list[EmployeeMetricSnapshot]:
    """Бэкфил истории: для каждой метрики ~12 точек на 6 месяцев назад с дрифтом.

    Нужен для timeseries-аналитики (ТЗ §14): без снимков графики Ai/Ri пустые
    до первого recompute.
    """
    snapshots: list[EmployeeMetricSnapshot] = []
    now = now_utc()
    for metric in metrics:
        for i in range(points):
            taken_at = now - timedelta(days=step_days * i, hours=rng.randint(0, 23))
            drift = rng.uniform(-0.1, 0.1)
            actuality = max(0.0, min(1.0, metric.actuality_score + drift))
            conflict = max(0.0, min(1.0, metric.conflict_rate + rng.uniform(-0.08, 0.08)))
            load = max(0.0, min(1.5, metric.load_level + rng.uniform(-0.1, 0.1)))
            risk = max(0.0, min(1.0, metric.risk_score + rng.uniform(-0.08, 0.08)))
            snapshots.append(
                EmployeeMetricSnapshot(
                    employee_id=metric.employee_id,
                    taken_at=taken_at,
                    days_since_update=max(0, metric.days_since_update + step_days * i),
                    actuality_score=round(actuality, 3),
                    outside_events_count=metric.outside_events_count,
                    total_events_count=metric.total_events_count,
                    conflict_rate=round(conflict, 3),
                    load_level=round(load, 3),
                    zone_factor=metric.zone_factor,
                    hr_factor=metric.hr_factor,
                    risk_score=round(risk, 3),
                    risk_level=_risk_level_for_score(risk),
                )
            )
    return snapshots


def _risk_level_for_score(score: float) -> str:
    if score >= 0.80:
        return "critical"
    if score >= 0.60:
        return "high"
    if score >= 0.35:
        return "medium"
    return "low"


def _days_back_for_index(i: int, n: int) -> int:
    """Распределяем last_updated_at от 1 до 100 дней — чтобы получить разные days_since_update."""
    # линейно: первая треть свежая, средняя — устаревшая, последняя — очень устаревшая
    third = n / 3
    if i < third:
        return random.randint(1, 14)
    if i < 2 * third:
        return random.randint(15, 60)
    return random.randint(60, 100)


def _translit(text_ru: str) -> str:
    table = {
        "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh",
        "з": "z", "и": "i", "й": "i", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o",
        "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "ts",
        "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu",
        "я": "ya",
    }
    out = []
    for ch in text_ru.lower():
        out.append(table.get(ch, ch))
    return "".join(out)


# ---------------------------------------------------------------------------
# Fixture loader (--from-files)
# ---------------------------------------------------------------------------

_DATE_PLACEHOLDER_RE = re.compile(r"\{\{(days_ago|days_from_now):(\d+)\}\}")


def _resolve_date_placeholders(value: str, *, today: date | None = None) -> str:
    """Заменяет {{days_ago:N}} / {{days_from_now:N}} на ISO-дату относительно сегодня.

    Дополнительный суффикс T-времени в значении сохраняется. Например
    '{{days_ago:14}}T09:00:00+00:00' → '2026-05-13T09:00:00+00:00'.
    """
    today = today or datetime.now(UTC).date()

    def repl(match: re.Match[str]) -> str:
        kind, n = match.group(1), int(match.group(2))
        delta = timedelta(days=n)
        target = today - delta if kind == "days_ago" else today + delta
        return target.isoformat()

    return _DATE_PLACEHOLDER_RE.sub(repl, value)


def _resolve_in_obj(obj: object) -> object:
    if isinstance(obj, str):
        return _resolve_date_placeholders(obj)
    if isinstance(obj, list):
        return [_resolve_in_obj(item) for item in obj]
    if isinstance(obj, dict):
        return {key: _resolve_in_obj(value) for key, value in obj.items()}
    return obj


def _parse_iso_dt(value: str) -> datetime:
    """ISO-строка → datetime с UTC, если tz не указан."""
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def _parse_time(value: str) -> time:
    return time.fromisoformat(value)


def _profile_dir(profile: str) -> Path:
    path = SEED_DATA_ROOT / profile
    if not path.is_dir():
        raise FileNotFoundError(f"fixture profile not found: {path}")
    return path


def _load_json(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    return _resolve_in_obj(data)  # type: ignore[return-value]


def load_fixtures(profile: str) -> dict[str, list[dict]]:
    base = _profile_dir(profile)
    fixtures: dict[str, list[dict]] = {}
    for name in (
        "employees",
        "teams",
        "team_members",
        "work_schedules",
        "schedule_exceptions",
    ):
        fixtures[name] = _load_json(base / f"{name}.json")
    events_csv = base / "activity_events.csv"
    if events_csv.exists():
        with events_csv.open(encoding="utf-8") as f:
            reader = csv.DictReader(f)
            fixtures["activity_events"] = [
                {
                    k: _resolve_date_placeholders(v) if isinstance(v, str) and v else v
                    for k, v in row.items()
                    if k is not None
                }
                for row in reader
            ]
    else:
        fixtures["activity_events"] = []
    return fixtures


def build_employees_from_fixtures(rows: list[dict]) -> list[Employee]:
    employees: list[Employee] = []
    for row in rows:
        kwargs = {
            "email": row.get("email"),
            "full_name": row["full_name"],
            "role": row["role"],
            "position": row.get("position"),
            "timezone": row["timezone"],
            "work_format": row["work_format"],
        }
        if "id" in row:
            kwargs["id"] = UUID(row["id"])
        if row.get("vk_user_id"):
            kwargs["vk_user_id"] = row["vk_user_id"]
        password = row.get("password")
        password_hash = row.get("password_hash")
        if password:
            kwargs["password_hash"] = hash_password(password)
        elif password_hash:
            kwargs["password_hash"] = password_hash
        employees.append(Employee(**kwargs))
    return employees


def build_teams_from_fixtures(rows: list[dict]) -> list[Team]:
    teams: list[Team] = []
    for row in rows:
        kwargs = {
            "name": row["name"],
            "description": row.get("description"),
        }
        if "id" in row:
            kwargs["id"] = UUID(row["id"])
        if row.get("avatar_url"):
            kwargs["avatar_url"] = row["avatar_url"]
        teams.append(Team(**kwargs))
    return teams


def build_team_members_from_fixtures(rows: list[dict]) -> list[TeamMember]:
    return [
        TeamMember(
            team_id=UUID(row["team_id"]),
            employee_id=UUID(row["employee_id"]),
            role_in_team=row.get("role_in_team", "member"),
        )
        for row in rows
    ]


def build_schedules_from_fixtures(rows: list[dict]) -> list[WorkSchedule]:
    schedules: list[WorkSchedule] = []
    for row in rows:
        confirmed = row.get("confirmed_at")
        schedules.append(
            WorkSchedule(
                employee_id=UUID(row["employee_id"]),
                work_days=row["work_days"],
                start_time=_parse_time(row["start_time"]),
                end_time=_parse_time(row["end_time"]),
                timezone=row["timezone"],
                work_format=row.get("work_format", "office"),
                last_updated_at=_parse_iso_dt(row["last_updated_at"]),
                confirmed_at=_parse_iso_dt(confirmed) if confirmed else None,
                is_active=row.get("is_active", True),
            )
        )
    return schedules


def build_exceptions_from_fixtures(rows: list[dict]) -> list[ScheduleException]:
    return [
        ScheduleException(
            employee_id=UUID(row["employee_id"]),
            type=row["type"],
            start_dt=_parse_iso_dt(row["start_dt"]),
            end_dt=_parse_iso_dt(row["end_dt"]),
            reason=row.get("reason"),
        )
        for row in rows
    ]


def build_events_from_fixtures(rows: list[dict]) -> list[ActivityEvent]:
    events: list[ActivityEvent] = []
    for row in rows:
        is_recurring_raw = (row.get("is_recurring") or "false").strip().lower()
        is_recurring = is_recurring_raw in {"true", "1", "yes"}
        is_outside_raw = (row.get("is_outside_schedule") or "").strip().lower()
        is_outside = is_outside_raw in {"true", "1", "yes"}
        events.append(
            ActivityEvent(
                employee_id=UUID(row["employee_id"]),
                external_id=row.get("external_id") or None,
                source=row.get("source") or "mock",
                event_type=row.get("event_type") or "meeting",
                title=row.get("title") or "Event",
                start_dt=_parse_iso_dt(row["start_dt"]),
                end_dt=_parse_iso_dt(row["end_dt"]),
                timezone=row.get("timezone") or "Europe/Moscow",
                recurrence_rule=row.get("recurrence_rule") or None,
                is_recurring=is_recurring,
                is_outside_schedule=is_outside,
            )
        )
    return events


# ---------------------------------------------------------------------------
# Roadmap generation
# ---------------------------------------------------------------------------


async def generate_roadmap_for_demo(
    session: AsyncSession, actor: Employee
) -> tuple[int, int]:
    """Создаёт roadmap items для всех сотрудников на основе их метрик.

    Возвращает (created, skipped). RoadmapService.generate сам коммитит.
    """
    service = RoadmapService(session)
    result = await service.generate(RoadmapGenerateRequest(), actor=actor)
    return result.created, result.skipped


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SeedResult:
    employees_created: int
    teams_created: int
    schedules_created: int
    events_created: int
    metrics_created: int
    snapshots_created: int
    confirmation_requests_created: int
    roadmap_created: int
    roadmap_skipped: int


async def run_seed(
    session: AsyncSession,
    *,
    reset: bool,
    small: bool,
    from_files: bool,
    with_roadmap: bool,
) -> SeedResult:
    """Создаёт демо-набор данных в переданной session.

    НЕ управляет жизненным циклом engine. Caller (CLI или HTTP-handler)
    отвечает за создание session и dispose engine.
    """
    rng = random.Random(SEED)
    random.seed(SEED)
    profile = "small" if small else "full"
    employee_limit = SMALL_EMPLOYEE_COUNT if small else None
    team_limit = SMALL_TEAM_COUNT if small else None

    if reset:
        print("Очищаю таблицы…")
        await reset_tables(session)

    if from_files:
        print(f"Загружаю фикстуры из scripts/seed_data/{profile}/…")
        fixtures = load_fixtures(profile)

        employees = build_employees_from_fixtures(fixtures["employees"])
        session.add_all(employees)
        await session.flush()
        print(f"  сотрудников: {len(employees)}")

        teams = build_teams_from_fixtures(fixtures["teams"])
        session.add_all(teams)
        await session.flush()
        print(f"  команд: {len(teams)}")

        members = build_team_members_from_fixtures(fixtures["team_members"])
        session.add_all(members)
        await session.flush()
        print(f"  team-членств: {len(members)}")

        schedules = build_schedules_from_fixtures(fixtures["work_schedules"])
        session.add_all(schedules)
        await session.flush()
        schedules_by_emp = {s.employee_id: s for s in schedules}
        print(f"  графиков: {len(schedules)}")

        exceptions = build_exceptions_from_fixtures(fixtures["schedule_exceptions"])
        session.add_all(exceptions)
        await session.flush()
        print(f"  исключений: {len(exceptions)}")

        events = build_events_from_fixtures(fixtures["activity_events"])
        session.add_all(events)
        await session.flush()
        print(f"  событий: {len(events)}")
    else:
        print("Создаю сотрудников…")
        employees = make_employees(rng, limit=employee_limit)
        session.add_all(employees)
        await session.flush()
        print(f"  сотрудников: {len(employees)}")

        print("Создаю команды…")
        teams = make_teams(limit=team_limit)
        session.add_all(teams)
        await session.flush()
        print(f"  команд: {len(teams)}")

        print("Назначаю участников команд…")
        members = make_team_members(rng, teams, employees)
        session.add_all(members)
        await session.flush()
        print(f"  team-членств: {len(members)}")

        print("Создаю рабочие графики…")
        schedules = make_work_schedules(rng, employees)
        session.add_all(schedules)
        await session.flush()
        schedules_by_emp = {s.employee_id: s for s in schedules}
        print(f"  графиков: {len(schedules)}")

        print("Создаю исключения (отпуска/больничные)…")
        exceptions = make_exceptions(rng, employees)
        session.add_all(exceptions)
        await session.flush()
        print(f"  исключений: {len(exceptions)}")

        print("Создаю события активности…")
        events = make_activity_events(rng, employees, schedules_by_emp)
        session.add_all(events)
        await session.flush()
        print(f"  событий: {len(events)}")

    # metrics всегда программно — выводятся из событий
    events_by_emp: dict[UUID, list[ActivityEvent]] = {}
    for ev in events:
        events_by_emp.setdefault(ev.employee_id, []).append(ev)
    print("Создаю метрики…")
    metrics = make_metrics(rng, employees, events_by_emp, schedules_by_emp)
    session.add_all(metrics)
    await session.flush()
    print(f"  метрик: {len(metrics)}")

    print("Создаю исторические снимки метрик…")
    snapshots = make_metric_snapshots(rng, metrics)
    session.add_all(snapshots)
    await session.flush()
    print(f"  снимков: {len(snapshots)}")

    print("Создаю запросы на подтверждение графика…")
    confirmation_requests = make_confirmation_requests(rng, employees, schedules_by_emp)
    session.add_all(confirmation_requests)
    await session.flush()
    print(f"  запросов: {len(confirmation_requests)}")

    admin = next(
        (e for e in employees if e.email == ADMIN_EMAIL),
        employees[0] if employees else None,
    )

    await session.commit()

    roadmap_created = 0
    roadmap_skipped = 0
    if with_roadmap and admin is not None:
        print("Генерирую дорожную карту актуализации…")
        roadmap_created, roadmap_skipped = await generate_roadmap_for_demo(session, admin)
        print(f"  roadmap items: created={roadmap_created} skipped={roadmap_skipped}")

    by_risk: dict[str, int] = {}
    for m in metrics:
        by_risk[m.risk_level] = by_risk.get(m.risk_level, 0) + 1
    print()
    print("Готово.")
    print("  демо-аккаунты (email / пароль / роль):")
    for email, password, role, *_ in DEMO_ACCOUNTS:
        print(f"    {email} / {password} / {role}")
    print(f"  распределение risk_level: {by_risk}")

    return SeedResult(
        employees_created=len(employees),
        teams_created=len(teams),
        schedules_created=len(schedules),
        events_created=len(events),
        metrics_created=len(metrics),
        snapshots_created=len(snapshots),
        confirmation_requests_created=len(confirmation_requests),
        roadmap_created=roadmap_created,
        roadmap_skipped=roadmap_skipped,
    )


async def seed(
    *,
    reset: bool,
    small: bool,
    from_files: bool,
    with_roadmap: bool,
) -> SeedResult:
    """CLI-wrapper: создаёт session, вызывает run_seed, диспозит engine."""
    async with AsyncSessionLocal() as session:
        result = await run_seed(
            session,
            reset=reset,
            small=small,
            from_files=from_files,
            with_roadmap=with_roadmap,
        )
    await engine.dispose()
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed WorkTime-Sync DB with demo data")
    parser.add_argument("--reset", action="store_true", help="TRUNCATE all tables before seeding")
    parser.add_argument(
        "--small",
        action="store_true",
        help=f"{SMALL_EMPLOYEE_COUNT} сотрудников + {SMALL_TEAM_COUNT} команды (вместо 30+5)",
    )
    parser.add_argument(
        "--from-files",
        dest="from_files",
        action="store_true",
        help="читать фикстуры из scripts/seed_data/{small|full}/ вместо программной генерации",
    )
    parser.add_argument(
        "--no-roadmap",
        dest="with_roadmap",
        action="store_false",
        default=True,
        help="не вызывать RoadmapService.generate() в конце",
    )
    args = parser.parse_args()
    asyncio.run(
        seed(
            reset=args.reset,
            small=args.small,
            from_files=args.from_files,
            with_roadmap=args.with_roadmap,
        )
    )


if __name__ == "__main__":
    main()
