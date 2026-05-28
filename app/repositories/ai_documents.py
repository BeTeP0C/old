from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai_document import AiDocument


class AiDocumentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, document: AiDocument) -> AiDocument:
        self.session.add(document)
        await self.session.flush()
        await self.session.refresh(document)
        return document

    async def get(self, document_id: UUID) -> AiDocument | None:
        return await self.session.get(AiDocument, document_id)

    async def list(self) -> list[AiDocument]:
        result = await self.session.execute(
            select(AiDocument).order_by(AiDocument.created_at.desc())
        )
        return list(result.scalars().all())

    async def delete(self, document: AiDocument) -> None:
        await self.session.delete(document)
        await self.session.flush()

    async def get_by_hash(self, content_hash: str) -> AiDocument | None:
        result = await self.session.execute(
            select(AiDocument).where(AiDocument.content_hash == content_hash)
        )
        return result.scalar_one_or_none()
