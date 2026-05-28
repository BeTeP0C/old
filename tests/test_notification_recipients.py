"""Unit-тесты для скоринга получателей (без БД, фейковые репозитории)."""

from dataclasses import dataclass, field
from uuid import UUID, uuid4

import pytest

from app.core.roles import EmployeeRole
from app.models.notification import (
    NOTIFICATION_SEVERITY_CRITICAL,
    NOTIFICATION_SEVERITY_HIGH,
    NOTIFICATION_SEVERITY_LOW,
    NOTIFICATION_SEVERITY_MEDIUM,
)
from app.services.notification_recipients import RecipientResolver


@dataclass
class FakeEmployee:
    id: UUID
    role: EmployeeRole
    full_name: str = "Test"
    timezone: str = "Europe/Moscow"


@dataclass
class FakeEmployeesRepo:
    employees: dict[UUID, FakeEmployee] = field(default_factory=dict)

    async def get(self, employee_id: UUID) -> FakeEmployee | None:
        return self.employees.get(employee_id)

    async def list_by_ids(self, ids: list[UUID]) -> list[FakeEmployee]:
        return [self.employees[i] for i in ids if i in self.employees]

    async def list(self, **_: object) -> list[FakeEmployee]:
        return list(self.employees.values())


@dataclass
class FakeTeamMembersRepo:
    by_team: dict[UUID, list[UUID]] = field(default_factory=dict)
    by_employee: dict[UUID, list[UUID]] = field(default_factory=dict)

    async def list_employee_ids_for_team(self, team_id: UUID) -> list[UUID]:
        return list(self.by_team.get(team_id, []))

    async def list_team_ids_for_employee(self, employee_id: UUID) -> list[UUID]:
        return list(self.by_employee.get(employee_id, []))


def _build(*emps: FakeEmployee, team_id: UUID | None = None) -> tuple[
    RecipientResolver, FakeEmployeesRepo, FakeTeamMembersRepo
]:
    e_repo = FakeEmployeesRepo({e.id: e for e in emps})
    tm_repo = FakeTeamMembersRepo()
    if team_id is not None:
        tm_repo.by_team[team_id] = [e.id for e in emps]
        for emp in emps:
            tm_repo.by_employee[emp.id] = [team_id]
    resolver = RecipientResolver(employees=e_repo, team_members=tm_repo)
    return resolver, e_repo, tm_repo


# --- employee event -------------------------------------------------------


@pytest.mark.asyncio
async def test_employee_event_low_severity_notifies_only_employee() -> None:
    employee = FakeEmployee(id=uuid4(), role=EmployeeRole.EMPLOYEE)
    resolver, *_ = _build(employee)
    result = await resolver.for_employee_event(
        employee_id=employee.id, severity=NOTIFICATION_SEVERITY_LOW
    )
    assert [r.id for r in result] == [employee.id]


@pytest.mark.asyncio
async def test_employee_event_high_severity_adds_manager() -> None:
    employee = FakeEmployee(id=uuid4(), role=EmployeeRole.EMPLOYEE)
    manager = FakeEmployee(id=uuid4(), role=EmployeeRole.MANAGER)
    team_id = uuid4()
    resolver, *_ = _build(employee, manager, team_id=team_id)
    result = await resolver.for_employee_event(
        employee_id=employee.id, severity=NOTIFICATION_SEVERITY_HIGH
    )
    assert {r.id for r in result} == {employee.id, manager.id}


@pytest.mark.asyncio
async def test_employee_event_critical_escalates_to_hr() -> None:
    employee = FakeEmployee(id=uuid4(), role=EmployeeRole.EMPLOYEE)
    manager = FakeEmployee(id=uuid4(), role=EmployeeRole.MANAGER)
    hr = FakeEmployee(id=uuid4(), role=EmployeeRole.HR)
    team_id = uuid4()
    resolver, *_ = _build(employee, manager, hr, team_id=team_id)
    result = await resolver.for_employee_event(
        employee_id=employee.id, severity=NOTIFICATION_SEVERITY_CRITICAL
    )
    assert {r.id for r in result} == {employee.id, manager.id, hr.id}


@pytest.mark.asyncio
async def test_employee_event_unknown_employee_returns_empty() -> None:
    resolver, *_ = _build()
    result = await resolver.for_employee_event(
        employee_id=uuid4(), severity=NOTIFICATION_SEVERITY_HIGH
    )
    assert result == []


# --- team event -----------------------------------------------------------


@pytest.mark.asyncio
async def test_team_event_picks_one_manager_not_all_roles() -> None:
    manager = FakeEmployee(id=uuid4(), role=EmployeeRole.MANAGER)
    pm = FakeEmployee(id=uuid4(), role=EmployeeRole.PM)
    hr = FakeEmployee(id=uuid4(), role=EmployeeRole.HR)
    extra_emp = FakeEmployee(id=uuid4(), role=EmployeeRole.EMPLOYEE)
    team_id = uuid4()
    resolver, *_ = _build(manager, pm, hr, extra_emp, team_id=team_id)
    result = await resolver.for_team_event(
        team_id=team_id, severity=NOTIFICATION_SEVERITY_MEDIUM
    )
    # Только один MANAGER, без HR/PM/employee — это и есть «не шум».
    assert [r.id for r in result] == [manager.id]


@pytest.mark.asyncio
async def test_team_event_falls_back_to_pm_when_no_manager() -> None:
    pm = FakeEmployee(id=uuid4(), role=EmployeeRole.PM)
    emp = FakeEmployee(id=uuid4(), role=EmployeeRole.EMPLOYEE)
    team_id = uuid4()
    resolver, *_ = _build(pm, emp, team_id=team_id)
    result = await resolver.for_team_event(
        team_id=team_id, severity=NOTIFICATION_SEVERITY_HIGH
    )
    assert [r.id for r in result] == [pm.id]


@pytest.mark.asyncio
async def test_team_event_critical_adds_hr() -> None:
    manager = FakeEmployee(id=uuid4(), role=EmployeeRole.MANAGER)
    hr = FakeEmployee(id=uuid4(), role=EmployeeRole.HR)
    team_id = uuid4()
    resolver, *_ = _build(manager, hr, team_id=team_id)
    result = await resolver.for_team_event(
        team_id=team_id, severity=NOTIFICATION_SEVERITY_CRITICAL
    )
    assert {r.id for r in result} == {manager.id, hr.id}


@pytest.mark.asyncio
async def test_empty_team_returns_empty() -> None:
    resolver, *_ = _build()
    result = await resolver.for_team_event(
        team_id=uuid4(), severity=NOTIFICATION_SEVERITY_HIGH
    )
    assert result == []
