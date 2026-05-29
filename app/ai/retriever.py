from datetime import date, datetime, time, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.activity_event import ActivityEvent
from app.repositories.employee_metrics import EmployeeMetricRepository
from app.repositories.employees import EmployeeRepository
from app.repositories.team_members import TeamMemberRepository
from app.repositories.teams import TeamRepository
from app.repositories.work_schedules import WorkScheduleRepository
from app.schemas.availability import MeetingRecommendationRequest
from app.services.exceptions import InvalidOperationError, NotFoundError
from app.services.recommendations import RecommendationService
from app.services.team_availability import TeamAvailabilityService

# Горизонт и параметры подбора окон для AI-контекста.
AVAILABILITY_HORIZON_DAYS = 7
DEFAULT_MEETING_DURATION_MINUTES = 60
MAX_TEAMS_IN_OVERVIEW = 8
# Большинство сотрудников в МСК — считаем окна в этом поясе, чтобы AI называл
# человекочитаемое время; абсолютные инстанты для других поясов не страдают.
_DISPLAY_TZ = ZoneInfo("Europe/Moscow")

# Стемы «вопрос про доступность/встречу». ВАЖНО: глагол «встретиться/встретимся»
# имеет основу «встрет», а существительное «встреча/встрече» — «встреч»; нужны оба,
# иначе самые частые формулировки не распознаются.
_SCHEDULING_KEYWORDS = (
    "встрет",
    "встреч",
    "созвон",
    "созвон",
    "созвонит",
    "позвон",
    "свобод",
    "доступ",
    "расписан",
    "слот",
    "подключ",
    "кворум",
    "окно",
    "окна",
    "удобн",
    "пересеч",
    "перекрыт",
    "когда смож",
    "когда получится",
    "во сколько",
    "созвониться",
    "meeting",
    "available",
    "availability",
    "schedule",
    "slot",
    "call",
    "free time",
    "when can",
    "what time",
)


def is_scheduling_question(text: str | None) -> bool:
    if not text:
        return False
    low = text.lower()
    return any(keyword in low for keyword in _SCHEDULING_KEYWORDS)


_RU_WEEKDAYS = (
    "понедельник",
    "вторник",
    "среда",
    "четверг",
    "пятница",
    "суббота",
    "воскресенье",
)
_RU_MONTHS = (
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)


def _format_slot_label(start: datetime, end: datetime) -> str:
    """Готовая русская подпись слота по Москве, например
    «среда, 3 июня, 11:30–12:30 (МСК)»."""
    local = start.astimezone(_DISPLAY_TZ)
    local_end = end.astimezone(_DISPLAY_TZ)
    return (
        f"{_RU_WEEKDAYS[local.weekday()]}, {local.day} {_RU_MONTHS[local.month - 1]}, "
        f"{local:%H:%M}–{local_end:%H:%M} (МСК)"
    )


class AiContextRetriever:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.employees = EmployeeRepository(session)
        self.teams = TeamRepository(session)
        self.team_members = TeamMemberRepository(session)
        self.metrics = EmployeeMetricRepository(session)
        self.schedules = WorkScheduleRepository(session)
        self.recommendations = RecommendationService(session)

    async def get_employee_context(
        self,
        employee_id: UUID,
        include_availability: bool = False,
    ) -> dict[str, Any]:
        employee = await self.employees.get(employee_id)
        if employee is None:
            raise NotFoundError("employee not found")
        metric = await self.metrics.get_for_employee(employee_id)
        schedule = await self.schedules.get_active_for_employee(employee_id)
        events = await self.get_recent_employee_events(employee_id)
        recommendations = await self.recommendations.list_for_employee(employee_id)
        context: dict[str, Any] = {
            "employee": _model_dict(
                employee,
                ("id", "role", "full_name", "position", "timezone", "work_format"),
            ),
            "active_schedule": _model_dict(
                schedule,
                ("id", "work_days", "start_time", "end_time", "timezone", "last_updated_at"),
            ),
            "employee_metrics": _model_dict(
                metric,
                (
                    "id",
                    "calculated_at",
                    "days_since_update",
                    "actuality_score",
                    "outside_events_count",
                    "total_events_count",
                    "conflict_rate",
                    "load_level",
                    "risk_score",
                    "risk_level",
                ),
            ),
            "recent_activity_events": [
                _model_dict(
                    event,
                    (
                        "id",
                        "source",
                        "event_type",
                        "title",
                        "start_dt",
                        "end_dt",
                        "timezone",
                        "is_outside_schedule",
                    ),
                )
                for event in events
            ],
            "rule_based_recommendations": [
                recommendation.model_dump(mode="json") for recommendation in recommendations
            ],
        }
        if include_availability:
            context["availability_next_7_days"] = await self._employee_availability(employee_id)
        return context

    async def get_team_context(self, team_id: UUID) -> dict[str, Any]:
        team = await self.teams.get(team_id)
        if team is None:
            raise NotFoundError("team not found")

        employee_ids = await self.team_members.list_employee_ids_for_team(team_id)
        employees = await self.employees.list_by_ids(employee_ids)
        metrics_by_employee = {
            metric.employee_id: metric
            for metric in await self.metrics.list_for_employees(employee_ids)
        }
        schedules_by_employee = {
            schedule.employee_id: schedule
            for schedule in await self.schedules.list_active_for_employees(employee_ids)
        }
        recommendations = await self.recommendations.list_for_team(team_id)
        info_by_id = {
            employee.id: {"name": employee.full_name, "position": employee.position}
            for employee in employees
        }
        meeting_options = await self._team_meeting_options(
            team_id, team.name, list(employee_ids), info_by_id
        )
        return {
            "team": _model_dict(team, ("id", "name", "description")),
            "meeting_options_next_7_days": meeting_options,
            "members": [
                {
                    "employee": _model_dict(
                        employee,
                        ("id", "role", "full_name", "position", "timezone", "work_format"),
                    ),
                    "employee_metrics": _model_dict(
                        metrics_by_employee.get(employee.id),
                        (
                            "id",
                            "calculated_at",
                            "actuality_score",
                            "conflict_rate",
                            "load_level",
                            "risk_score",
                            "risk_level",
                        ),
                    ),
                    "active_schedule": _model_dict(
                        schedules_by_employee.get(employee.id),
                        ("id", "work_days", "start_time", "end_time", "timezone"),
                    ),
                }
                for employee in employees
            ],
            "rule_based_recommendations": [
                recommendation.model_dump(mode="json") for recommendation in recommendations
            ],
        }

    async def get_overview_context(
        self,
        top_n: int = 5,
        question: str | None = None,
    ) -> dict[str, Any]:
        """Срез по всем сотрудникам для general-вопросов HR (§16 п.1, п.3).

        Возвращает агрегаты + топы по нагрузке / устаревшим графикам /
        конфликтности — чтобы LLM мог отвечать на вопросы вида
        «кто перегружен?» / «у кого устарел график?» без явного employee_id.

        Если вопрос про доступность/встречи — дополнительно подгружает подбор
        окон по командам, чтобы AI мог назвать конкретное время без выбора скоупа.
        """
        employees = await self.employees.list()
        if not employees:
            return {"question_scope": "general", "employees_total": 0}

        employee_by_id = {employee.id: employee for employee in employees}
        metrics = await self.metrics.list_for_employees(list(employee_by_id))

        def _row(metric: object) -> dict[str, Any]:
            employee = employee_by_id.get(metric.employee_id)  # type: ignore[attr-defined]
            return {
                "employee_id": str(metric.employee_id),  # type: ignore[attr-defined]
                "full_name": employee.full_name if employee else None,
                "position": employee.position if employee else None,
                "actuality_score": metric.actuality_score,  # type: ignore[attr-defined]
                "load_level": metric.load_level,  # type: ignore[attr-defined]
                "conflict_rate": metric.conflict_rate,  # type: ignore[attr-defined]
                "days_since_update": metric.days_since_update,  # type: ignore[attr-defined]
                "risk_level": metric.risk_level,  # type: ignore[attr-defined]
                "risk_score": metric.risk_score,  # type: ignore[attr-defined]
            }

        rows = [_row(metric) for metric in metrics]

        risk_breakdown = {"low": 0, "medium": 0, "high": 0, "critical": 0}
        for row in rows:
            level = row["risk_level"]
            if level in risk_breakdown:
                risk_breakdown[level] += 1

        overloaded = sorted(
            (row for row in rows if row["load_level"] > 0.8),
            key=lambda r: r["load_level"],
            reverse=True,
        )[:top_n]
        outdated = sorted(
            (row for row in rows if row["actuality_score"] < 0.7),
            key=lambda r: r["actuality_score"],
        )[:top_n]
        high_conflict = sorted(
            (row for row in rows if row["conflict_rate"] > 0.15),
            key=lambda r: r["conflict_rate"],
            reverse=True,
        )[:top_n]
        highest_risk = sorted(rows, key=lambda r: r["risk_score"], reverse=True)[:top_n]

        overview: dict[str, Any] = {
            "question_scope": "general",
            "employees_total": len(employees),
            "employees_with_metrics": len(metrics),
            "risk_level_breakdown": risk_breakdown,
            "top_overloaded": overloaded,
            "top_outdated_schedules": outdated,
            "top_conflicts": high_conflict,
            "top_risk": highest_risk,
        }
        if is_scheduling_question(question):
            info_by_id = {
                employee.id: {"name": employee.full_name, "position": employee.position}
                for employee in employees
            }
            overview["team_meeting_options"] = await self._all_teams_meeting_options(info_by_id)
        return overview

    def _upcoming_range(self) -> tuple[datetime, datetime]:
        now = datetime.now(_DISPLAY_TZ).replace(minute=0, second=0, microsecond=0)
        return now, now + timedelta(days=AVAILABILITY_HORIZON_DAYS)

    async def _employee_availability(self, employee_id: UUID) -> list[dict[str, str]]:
        start, end = self._upcoming_range()
        service = TeamAvailabilityService(self.session)
        try:
            windows = await service.get_employee_availability(employee_id, start, end)
        except (NotFoundError, InvalidOperationError):
            return []
        return [
            {"start": window.start_dt.isoformat(), "end": window.end_dt.isoformat()}
            for window in windows
        ]

    async def _team_meeting_options(
        self,
        team_id: UUID,
        team_name: str,
        member_ids: list[UUID],
        info_by_id: dict[UUID, dict[str, str]],
    ) -> dict[str, Any] | None:
        if not member_ids:
            return None
        start, end = self._upcoming_range()
        service = TeamAvailabilityService(self.session)
        try:
            recommendations = await service.recommend_meetings(
                team_id,
                MeetingRecommendationRequest(
                    start_dt=start,
                    end_dt=end,
                    duration_minutes=DEFAULT_MEETING_DURATION_MINUTES,
                    optional_employee_ids=member_ids,
                ),
            )
        except (NotFoundError, InvalidOperationError):
            return None
        slots = [
            {
                # human_label — готовая русская дата по Москве, чтобы слабая модель
                # не выдумывала день/месяц из ISO. Модель должна цитировать его как есть.
                "human_label": _format_slot_label(
                    recommendation.start_dt, recommendation.end_dt
                ),
                "start": recommendation.start_dt.isoformat(),
                "end": recommendation.end_dt.isoformat(),
                "available_count": len(recommendation.available_employee_ids),
                "team_size": len(member_ids),
                "available_employees": [
                    info_by_id[eid]["name"]
                    for eid in recommendation.available_employee_ids
                    if eid in info_by_id
                ],
            }
            for recommendation in recommendations
        ]
        return {
            "team_id": str(team_id),
            "team_name": team_name,
            "meeting_duration_minutes": DEFAULT_MEETING_DURATION_MINUTES,
            # Состав команды — чтобы AI знал, КТО входит в «команду разработки».
            "members": [
                info_by_id[mid] for mid in member_ids if mid in info_by_id
            ],
            "best_slots": slots,
            "note": (
                "best_slots — реальные свободные окна, рассчитанные системой "
                "(рабочие графики минус занятость) на ближайшие 7 дней. "
                "available_count из team_size человек смогут подключиться."
            ),
        }

    async def _all_teams_meeting_options(
        self,
        info_by_id: dict[UUID, dict[str, str]],
    ) -> list[dict[str, Any]]:
        teams = await self.teams.list()
        options: list[dict[str, Any]] = []
        for team in teams[:MAX_TEAMS_IN_OVERVIEW]:
            member_ids = await self.team_members.list_employee_ids_for_team(team.id)
            team_options = await self._team_meeting_options(
                team.id, team.name, member_ids, info_by_id
            )
            if team_options is not None:
                options.append(team_options)
        return options

    async def get_recent_employee_events(
        self,
        employee_id: UUID,
        limit: int = 20,
    ) -> list[ActivityEvent]:
        result = await self.session.execute(
            select(ActivityEvent)
            .where(ActivityEvent.employee_id == employee_id)
            .order_by(ActivityEvent.start_dt.desc())
            .limit(limit)
        )
        return list(result.scalars().all())


def _model_dict(model: object | None, fields: tuple[str, ...]) -> dict[str, Any] | None:
    if model is None:
        return None
    return {field: _json_value(getattr(model, field)) for field in fields}


def _json_value(value: object) -> object:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime | date | time):
        return value.isoformat()
    return value
