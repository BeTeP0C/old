from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.team_member import TeamMember


class TeamMemberRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, team_member: TeamMember) -> TeamMember:
        self.session.add(team_member)
        await self.session.flush()
        await self.session.refresh(team_member)
        return team_member

    async def get(self, team_id: UUID, employee_id: UUID) -> TeamMember | None:
        return await self.session.get(
            TeamMember,
            {"team_id": team_id, "employee_id": employee_id},
        )

    async def list_employee_ids_for_team(self, team_id: UUID) -> list[UUID]:
        result = await self.session.execute(
            select(TeamMember.employee_id).where(TeamMember.team_id == team_id)
        )
        return list(result.scalars().all())

    async def list_team_ids_for_employee(self, employee_id: UUID) -> list[UUID]:
        result = await self.session.execute(
            select(TeamMember.team_id).where(TeamMember.employee_id == employee_id)
        )
        return list(result.scalars().all())

    async def count_for_team(self, team_id: UUID) -> int:
        result = await self.session.execute(
            select(func.count())
            .select_from(TeamMember)
            .where(TeamMember.team_id == team_id)
        )
        return int(result.scalar_one())

    async def counts_by_team(self, team_ids: list[UUID]) -> dict[UUID, int]:
        if not team_ids:
            return {}
        result = await self.session.execute(
            select(TeamMember.team_id, func.count())
            .where(TeamMember.team_id.in_(team_ids))
            .group_by(TeamMember.team_id)
        )
        return {team_id: int(count) for team_id, count in result.all()}

    async def delete(self, team_member: TeamMember) -> None:
        await self.session.delete(team_member)
        await self.session.flush()
