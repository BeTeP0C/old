from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import undefer

from app.models.import_log import ImportLog


class ImportLogRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, entry: ImportLog) -> ImportLog:
        self.session.add(entry)
        await self.session.flush()
        await self.session.refresh(entry)
        return entry

    async def count(self) -> int:
        result = await self.session.execute(select(func.count()).select_from(ImportLog))
        return int(result.scalar_one())

    async def list(self, *, skip: int = 0, limit: int = 20) -> list[ImportLog]:
        stmt = (
            select(ImportLog)
            # id как вторичный ключ сортировки — детерминированная пагинация при
            # совпадающих created_at (массовые импорты в один момент).
            .order_by(ImportLog.created_at.desc(), ImportLog.id.desc())
            .offset(skip)
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_with_content(self, import_id: UUID) -> ImportLog | None:
        # undefer — явно подгружаем content (он deferred) для скачивания.
        stmt = (
            select(ImportLog)
            .options(undefer(ImportLog.content))
            .where(ImportLog.id == import_id)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()
