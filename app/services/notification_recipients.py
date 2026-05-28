"""Скоринг получателей уведомления.

Раньше для team-итемов рассылка шла всем `{MANAGER, PM, HR}` команды — это
«шум». Здесь мы решаем, кому именно отправить, на основе типа события и его
severity:

* employee-уровень: сам сотрудник + один руководитель его команды (если есть).
* team-уровень: один MANAGER (или PM, если менеджера нет). Для severity=critical
  эскалируем дополнительно на HR.

Это закрывает «ИИ должен выбирать адресата» (§16 п.6 ТЗ) на простой rule-engine
основе — без LLM-вызова, чтобы быть детерминированно и дёшево. LLM подключается
отдельно для генерации body (см. notification_ai.py).
"""

from __future__ import annotations

from uuid import UUID

from app.core.roles import EmployeeRole
from app.models.employee import Employee
from app.models.notification import (
    NOTIFICATION_SEVERITY_CRITICAL,
    NOTIFICATION_SEVERITY_HIGH,
)
from app.repositories.employees import EmployeeRepository
from app.repositories.team_members import TeamMemberRepository


class RecipientResolver:
    def __init__(
        self,
        *,
        employees: EmployeeRepository,
        team_members: TeamMemberRepository,
    ) -> None:
        self.employees = employees
        self.team_members = team_members

    async def for_employee_event(
        self,
        *,
        employee_id: UUID,
        severity: str,
    ) -> list[Employee]:
        """Кого уведомить о событии, привязанном к конкретному сотруднику."""
        employee = await self.employees.get(employee_id)
        if employee is None:
            return []

        recipients: list[Employee] = [employee]

        # Эскалация на руководителя при high/critical.
        if severity in {NOTIFICATION_SEVERITY_HIGH, NOTIFICATION_SEVERITY_CRITICAL}:
            manager = await self._pick_manager_for_employee(employee_id)
            if manager is not None and manager.id != employee.id:
                recipients.append(manager)

        # Critical дополнительно эскалируем на HR.
        if severity == NOTIFICATION_SEVERITY_CRITICAL:
            hr = await self._pick_role(EmployeeRole.HR, exclude_ids={r.id for r in recipients})
            if hr is not None:
                recipients.append(hr)

        return _dedup_preserve_order(recipients)

    async def for_team_event(
        self,
        *,
        team_id: UUID,
        severity: str,
    ) -> list[Employee]:
        """Кого уведомить о событии, привязанном к команде целиком."""
        member_ids = await self.team_members.list_employee_ids_for_team(team_id)
        if not member_ids:
            return []
        members = await self.employees.list_by_ids(member_ids)

        # Один MANAGER из команды; если менеджера нет — PM.
        manager = _first_with_role(members, EmployeeRole.MANAGER) or _first_with_role(
            members, EmployeeRole.PM
        )
        recipients: list[Employee] = []
        if manager is not None:
            recipients.append(manager)

        if severity == NOTIFICATION_SEVERITY_CRITICAL:
            hr = await self._pick_role(
                EmployeeRole.HR, exclude_ids={r.id for r in recipients}
            )
            if hr is not None:
                recipients.append(hr)

        return _dedup_preserve_order(recipients)

    async def _pick_manager_for_employee(self, employee_id: UUID) -> Employee | None:
        """Находит первого руководителя в любой из команд сотрудника.

        Без сложного ранжирования — для MVP достаточно «первый найденный».
        """
        team_ids = await self.team_members.list_team_ids_for_employee(employee_id)
        for team_id in team_ids:
            member_ids = await self.team_members.list_employee_ids_for_team(team_id)
            members = await self.employees.list_by_ids(member_ids)
            manager = _first_with_role(members, EmployeeRole.MANAGER)
            if manager is not None and manager.id != employee_id:
                return manager
        return None

    async def _pick_role(
        self, role: EmployeeRole, *, exclude_ids: set[UUID]
    ) -> Employee | None:
        # EmployeeRepository.list не умеет фильтровать по role напрямую;
        # для MVP компании из сотен сотрудников фильтрация в памяти приемлема.
        employees = await self.employees.list()
        for emp in employees:
            if emp.role == role and emp.id not in exclude_ids:
                return emp
        return None


def _first_with_role(employees: list[Employee], role: EmployeeRole) -> Employee | None:
    for emp in employees:
        if emp.role == role:
            return emp
    return None


def _dedup_preserve_order(employees: list[Employee]) -> list[Employee]:
    seen: set[UUID] = set()
    result: list[Employee] = []
    for emp in employees:
        if emp.id in seen:
            continue
        seen.add(emp.id)
        result.append(emp)
    return result
