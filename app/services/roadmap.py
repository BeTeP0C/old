from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from dataclasses import asdict as dataclass_asdict
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.analytics.metrics import MetricSnapshot
from app.analytics.roadmap_priority import (
    SEVERITY_TO_DUE_DAYS,
    PriorityInputs,
    build_reason,
    compute_priority,
)
from app.models.employee import Employee
from app.models.employee_metric import EmployeeMetric
from app.models.roadmap_item import (
    ROADMAP_STATUS_ACKNOWLEDGED,
    ROADMAP_STATUS_COMPLETED,
    ROADMAP_STATUS_DEFERRED,
    ROADMAP_STATUS_DISMISSED,
    ROADMAP_STATUS_IGNORED,
    ROADMAP_STATUS_PENDING,
    ROADMAP_STATUS_REQUESTED,
    ROADMAP_STATUS_UPDATED,
    ROADMAP_SUBJECT_EMPLOYEE,
    ROADMAP_SUBJECT_TEAM,
    RoadmapItem,
)
from app.models.schedule_confirmation_request import (
    CONFIRMATION_STATUS_PENDING,
    ScheduleConfirmationRequest,
)
from app.repositories.employee_metrics import EmployeeMetricRepository
from app.repositories.employees import EmployeeRepository
from app.repositories.roadmap_items import RoadmapItemRepository
from app.repositories.team_members import TeamMemberRepository
from app.repositories.teams import TeamRepository
from app.schemas.recommendation import RecommendationResponse
from app.services.exceptions import InvalidOperationError, NotFoundError
from app.services.notifications import NotificationService
from app.services.recommendations import (
    RecommendationService,
    metric_snapshot_from_orm,
)


STATUS_TRANSITIONS: dict[str, frozenset[str]] = {
    ROADMAP_STATUS_PENDING: frozenset(
        {
            ROADMAP_STATUS_REQUESTED,
            ROADMAP_STATUS_DEFERRED,
            ROADMAP_STATUS_IGNORED,
            ROADMAP_STATUS_DISMISSED,
            ROADMAP_STATUS_COMPLETED,
        }
    ),
    ROADMAP_STATUS_REQUESTED: frozenset(
        {
            ROADMAP_STATUS_ACKNOWLEDGED,
            ROADMAP_STATUS_COMPLETED,
            ROADMAP_STATUS_DEFERRED,
        }
    ),
    ROADMAP_STATUS_ACKNOWLEDGED: frozenset(
        {
            ROADMAP_STATUS_UPDATED,
            ROADMAP_STATUS_COMPLETED,
            ROADMAP_STATUS_DEFERRED,
        }
    ),
    ROADMAP_STATUS_UPDATED: frozenset({ROADMAP_STATUS_COMPLETED}),
    ROADMAP_STATUS_DEFERRED: frozenset(
        {
            ROADMAP_STATUS_PENDING,
            ROADMAP_STATUS_REQUESTED,
            ROADMAP_STATUS_IGNORED,
            ROADMAP_STATUS_DISMISSED,
        }
    ),
    ROADMAP_STATUS_COMPLETED: frozenset(),
    # Из IGNORED разрешаем возврат в PENDING — UI «Восстановить» для
    # проигнорированных рекомендаций. Раньше это был final-state и фронт
    # получал 400 при попытке вернуть.
    ROADMAP_STATUS_IGNORED: frozenset({ROADMAP_STATUS_PENDING}),
    ROADMAP_STATUS_DISMISSED: frozenset(),
}


@dataclass(frozen=True, slots=True)
class RoadmapListFilters:
    statuses: list[str] | None = None
    severities: list[str] | None = None
    subject_type: str | None = None
    team_id: UUID | None = None
    employee_id: UUID | None = None
    assigned_to_id: UUID | None = None
    search: str | None = None
    include_closed: bool = False
    limit: int = 50
    offset: int = 0


@dataclass(frozen=True, slots=True)
class RoadmapListResult:
    items: list[RoadmapItem]
    total: int
    counts_by_status: dict[str, int]
    counts_by_severity: dict[str, int]


@dataclass(frozen=True, slots=True)
class RoadmapGenerateRequest:
    team_id: UUID | None = None
    employee_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class RoadmapGenerateResult:
    created: int
    skipped: int
    items: list[RoadmapItem]


@dataclass(frozen=True, slots=True)
class RoadmapStatusUpdate:
    status: str
    notes: str | None = None


@dataclass(frozen=True, slots=True)
class RoadmapItemUpdate:
    notes: str | None = None
    assigned_to_id: UUID | None = None
    due_at: datetime | None = None


class RoadmapService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = RoadmapItemRepository(session)
        self.employees = EmployeeRepository(session)
        self.teams = TeamRepository(session)
        self.team_members = TeamMemberRepository(session)
        self.metrics = EmployeeMetricRepository(session)
        self.notifications = NotificationService(session)
        self.recommendations = RecommendationService(session)

    async def list(self, filters: RoadmapListFilters) -> RoadmapListResult:
        items = await self.repo.list(
            statuses=filters.statuses,
            severities=filters.severities,
            subject_type=filters.subject_type,
            team_id=filters.team_id,
            employee_id=filters.employee_id,
            assigned_to_id=filters.assigned_to_id,
            search=filters.search,
            include_closed=filters.include_closed,
            limit=filters.limit,
            offset=filters.offset,
        )
        total = await self.repo.count(
            statuses=filters.statuses,
            severities=filters.severities,
            subject_type=filters.subject_type,
            team_id=filters.team_id,
            employee_id=filters.employee_id,
            assigned_to_id=filters.assigned_to_id,
            search=filters.search,
            include_closed=filters.include_closed,
        )
        counts_by_status = await self.repo.aggregate_status_counts(
            team_id=filters.team_id, include_closed=True
        )
        counts_by_severity = await self.repo.aggregate_severity_counts(
            team_id=filters.team_id, include_closed=False
        )
        return RoadmapListResult(
            items=items,
            total=total,
            counts_by_status=counts_by_status,
            counts_by_severity=counts_by_severity,
        )

    async def get(self, item_id: UUID) -> RoadmapItem:
        item = await self.repo.get(item_id)
        if item is None:
            raise NotFoundError("roadmap item not found")
        return item

    async def list_for_team(
        self, team_id: UUID, filters: RoadmapListFilters
    ) -> RoadmapListResult:
        if await self.teams.get(team_id) is None:
            raise NotFoundError("team not found")
        return await self.list(
            RoadmapListFilters(
                statuses=filters.statuses,
                severities=filters.severities,
                subject_type=filters.subject_type,
                team_id=team_id,
                employee_id=filters.employee_id,
                assigned_to_id=filters.assigned_to_id,
                search=filters.search,
                include_closed=filters.include_closed,
                limit=filters.limit,
                offset=filters.offset,
            )
        )

    async def list_for_employee(
        self, employee_id: UUID, filters: RoadmapListFilters
    ) -> RoadmapListResult:
        if await self.employees.get(employee_id) is None:
            raise NotFoundError("employee not found")
        return await self.list(
            RoadmapListFilters(
                statuses=filters.statuses,
                severities=filters.severities,
                subject_type=filters.subject_type,
                team_id=filters.team_id,
                employee_id=employee_id,
                assigned_to_id=filters.assigned_to_id,
                search=filters.search,
                include_closed=filters.include_closed,
                limit=filters.limit,
                offset=filters.offset,
            )
        )

    async def generate(
        self, payload: RoadmapGenerateRequest, actor: Employee
    ) -> RoadmapGenerateResult:
        scope_employees = await self._resolve_scope(payload)
        if not scope_employees:
            return RoadmapGenerateResult(created=0, skipped=0, items=[])

        recommendations = await self.recommendations.recommend_for_employees(
            scope_employees
        )
        if not recommendations:
            return RoadmapGenerateResult(created=0, skipped=0, items=[])

        metrics_by_employee = {
            metric.employee_id: metric
            for metric in await self.metrics.list_for_employees(
                [emp.id for emp in scope_employees]
            )
        }

        subject_pairs = list({(r.subject_type, r.subject_id) for r in recommendations})
        existing_open = await self.repo.list_open_for_subjects(subject_pairs)
        open_keys: set[tuple[str, UUID, str]] = {
            (item.subject_type, item.subject_id, item.recommendation_code)
            for item in existing_open
        }

        new_items: list[RoadmapItem] = []
        skipped = 0
        now = datetime.now(UTC)

        for recommendation in recommendations:
            key = (
                recommendation.subject_type,
                recommendation.subject_id,
                recommendation.code,
            )
            if key in open_keys:
                skipped += 1
                continue

            item = self._build_pending_item(
                recommendation,
                actor,
                metrics_by_employee=metrics_by_employee,
                now=now,
            )
            new_items.append(item)
            open_keys.add(key)

        await self.repo.bulk_create(new_items)
        await self.session.commit()

        # Reload with relations for response
        loaded: list[RoadmapItem] = []
        for item in new_items:
            reloaded = await self.repo.get(item.id)
            if reloaded is not None:
                loaded.append(reloaded)

        return RoadmapGenerateResult(
            created=len(new_items), skipped=skipped, items=loaded
        )

    async def update_status(
        self,
        item_id: UUID,
        payload: RoadmapStatusUpdate,
        actor: Employee,
    ) -> RoadmapItem:
        item = await self.get(item_id)
        target = payload.status
        allowed = STATUS_TRANSITIONS.get(item.status, frozenset())
        if target not in allowed:
            raise InvalidOperationError(
                f"transition {item.status}→{target} not allowed"
            )

        now = datetime.now(UTC)
        item.status = target
        if payload.notes is not None:
            item.notes = payload.notes

        if target == ROADMAP_STATUS_REQUESTED:
            item.requested_at = now
            await self.notifications.create_for_roadmap_request(item, actor)
            if (
                item.recommendation_code == "outdated_schedule"
                and item.confirmation_request_id is None
                and item.employee_id is not None
            ):
                confirmation = ScheduleConfirmationRequest(
                    employee_id=item.employee_id,
                    requested_by_id=actor.id,
                    reason=item.reason,
                    status=CONFIRMATION_STATUS_PENDING,
                )
                self.session.add(confirmation)
                await self.session.flush()
                item.confirmation_request_id = confirmation.id
        elif target == ROADMAP_STATUS_ACKNOWLEDGED:
            item.acknowledged_at = now
        elif target == ROADMAP_STATUS_COMPLETED:
            item.completed_at = now

        await self.session.flush()
        await self.session.commit()
        return await self.get(item_id)

    async def update(
        self,
        item_id: UUID,
        payload: RoadmapItemUpdate,
        actor: Employee,
    ) -> RoadmapItem:
        item = await self.get(item_id)
        values: dict[str, object] = {}
        if payload.notes is not None:
            values["notes"] = payload.notes
        if payload.assigned_to_id is not None:
            if await self.employees.get(payload.assigned_to_id) is None:
                raise NotFoundError("assignee not found")
            values["assigned_to_id"] = payload.assigned_to_id
        if payload.due_at is not None:
            values["due_at"] = payload.due_at
        if values:
            await self.repo.update(item, values)
            await self.session.commit()
        return await self.get(item_id)

    async def delete(self, item_id: UUID, actor: Employee) -> None:
        item = await self.get(item_id)
        await self.repo.update(
            item,
            {
                "status": ROADMAP_STATUS_DISMISSED,
                "completed_at": datetime.now(UTC),
            },
        )
        await self.session.commit()

    async def recompute_priorities(self, *, team_id: UUID | None = None) -> int:
        open_items = await self.repo.list_all_open()
        if team_id is not None:
            member_ids = set(
                await self.team_members.list_employee_ids_for_team(team_id)
            )
            open_items = [
                item
                for item in open_items
                if item.team_id == team_id
                or (item.employee_id is not None and item.employee_id in member_ids)
            ]
        if not open_items:
            return 0

        employee_ids = list(
            {item.employee_id for item in open_items if item.employee_id is not None}
        )
        metrics_by_employee = {
            metric.employee_id: metric
            for metric in await self.metrics.list_for_employees(employee_ids)
        }
        now = datetime.now(UTC)
        updated = 0
        for item in open_items:
            snapshot = (
                metric_snapshot_from_orm(metrics_by_employee.get(item.employee_id))
                if item.employee_id is not None
                else None
            )
            days_since_request = (
                (now - item.requested_at).days if item.requested_at else 0
            )
            days_overdue = (
                max(0, (now - item.due_at).days) if item.due_at else 0
            )
            new_priority = compute_priority(
                PriorityInputs(
                    metric=snapshot,
                    severity=item.severity,
                    code=item.recommendation_code,
                    days_since_request=days_since_request,
                    days_overdue=days_overdue,
                )
            )
            if abs(new_priority - item.priority_score) >= 0.01:
                item.priority_score = new_priority
                updated += 1
        if updated:
            await self.session.commit()
        return updated

    def _build_pending_item(
        self,
        recommendation: RecommendationResponse,
        actor: Employee,
        *,
        metrics_by_employee: dict[UUID, "EmployeeMetric"] | None = None,
        now: datetime | None = None,
    ) -> RoadmapItem:
        """Создаёт (в памяти) RoadmapItem со status=pending из живой рекомендации."""
        metrics_map = metrics_by_employee or {}
        ts = now or datetime.now(UTC)
        snapshot = (
            metric_snapshot_from_orm(metrics_map.get(recommendation.subject_id))
            if recommendation.subject_type == ROADMAP_SUBJECT_EMPLOYEE
            else None
        )
        priority = compute_priority(
            PriorityInputs(
                metric=snapshot,
                severity=recommendation.severity,
                code=recommendation.code,
            )
        )
        due_days = SEVERITY_TO_DUE_DAYS.get(recommendation.severity, 10)
        return RoadmapItem(
            subject_type=recommendation.subject_type,
            subject_id=recommendation.subject_id,
            employee_id=(
                recommendation.subject_id
                if recommendation.subject_type == ROADMAP_SUBJECT_EMPLOYEE
                else None
            ),
            team_id=(
                recommendation.subject_id
                if recommendation.subject_type == ROADMAP_SUBJECT_TEAM
                else None
            ),
            recommendation_code=recommendation.code,
            severity=recommendation.severity,
            reason=build_reason(recommendation.code, snapshot, recommendation.reason),
            action=recommendation.action,
            priority_score=priority,
            status=ROADMAP_STATUS_PENDING,
            due_at=ts + timedelta(days=due_days),
            created_by_id=actor.id,
            metric_snapshot=_snapshot_to_dict(snapshot),
        )

    async def apply_recommendation_status(
        self,
        *,
        code: str,
        subject_type: str,
        subject_id: UUID,
        target_status: str,
        actor: Employee,
    ) -> RoadmapItem:
        """Auto-promote живой рекомендации в RoadmapItem и сразу транзитим статус.

        Если для (subject_type, subject_id, code) уже есть open-item — переиспользуем.
        Иначе валидируем что рекомендация реально вычисляется и создаём pending-item.
        """
        if subject_type not in (ROADMAP_SUBJECT_EMPLOYEE, ROADMAP_SUBJECT_TEAM):
            raise InvalidOperationError(f"unsupported subject_type: {subject_type}")

        item = await self.repo.find_open_by_recommendation(
            subject_type=subject_type,
            subject_id=subject_id,
            recommendation_code=code,
        )
        if item is None:
            recommendation = await self._find_live_recommendation(
                code=code,
                subject_type=subject_type,
                subject_id=subject_id,
            )
            if recommendation is None:
                raise NotFoundError("recommendation not found")
            metrics_map: dict[UUID, "EmployeeMetric"] = {}
            if subject_type == ROADMAP_SUBJECT_EMPLOYEE:
                metrics_map = {
                    metric.employee_id: metric
                    for metric in await self.metrics.list_for_employees([subject_id])
                }
            item = self._build_pending_item(
                recommendation,
                actor,
                metrics_by_employee=metrics_map,
            )
            await self.repo.create(item)
            await self.session.flush()
        return await self.update_status(
            item.id,
            RoadmapStatusUpdate(status=target_status),
            actor,
        )

    async def _find_live_recommendation(
        self,
        *,
        code: str,
        subject_type: str,
        subject_id: UUID,
    ) -> RecommendationResponse | None:
        if subject_type == ROADMAP_SUBJECT_EMPLOYEE:
            try:
                recs = await self.recommendations.list_for_employee(subject_id)
            except NotFoundError:
                return None
        elif subject_type == ROADMAP_SUBJECT_TEAM:
            try:
                recs = await self.recommendations.list_for_team(subject_id)
            except NotFoundError:
                return None
        else:
            return None
        for rec in recs:
            if rec.code == code and rec.subject_id == subject_id:
                return rec
        return None

    async def _resolve_scope(
        self, payload: RoadmapGenerateRequest
    ) -> list[Employee]:
        if payload.employee_id is not None:
            employee = await self.employees.get(payload.employee_id)
            if employee is None:
                raise NotFoundError("employee not found")
            return [employee]
        if payload.team_id is not None:
            if await self.teams.get(payload.team_id) is None:
                raise NotFoundError("team not found")
            employee_ids = await self.team_members.list_employee_ids_for_team(
                payload.team_id
            )
            return await self.employees.list_by_ids(employee_ids)
        return await self.employees.list()


def _snapshot_to_dict(snapshot: MetricSnapshot | None) -> dict | None:
    if snapshot is None:
        return None
    return dataclass_asdict(snapshot)


def _ensure_iterable(value: Iterable | None) -> list:
    return [] if value is None else list(value)
