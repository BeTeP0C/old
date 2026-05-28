from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ai_chunk import AiChunk


class AiChunkRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_many(self, chunks: list[AiChunk]) -> list[AiChunk]:
        self.session.add_all(chunks)
        await self.session.flush()
        for chunk in chunks:
            await self.session.refresh(chunk)
        return chunks

    async def list_by_document(self, document_id: UUID) -> list[AiChunk]:
        result = await self.session.execute(
            select(AiChunk)
            .where(AiChunk.document_id == document_id)
            .order_by(AiChunk.chunk_index)
        )
        return list(result.scalars().all())

    async def search_by_text(self, query: str, limit: int) -> list[AiChunk]:
        terms = [term for term in query.strip().split() if term]
        if not terms:
            return []
        conditions = [AiChunk.content.ilike(f"%{term}%") for term in terms]
        result = await self.session.execute(
            select(AiChunk)
            .where(*conditions)
            .order_by(AiChunk.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def search_by_embedding(self, embedding: list[float], limit: int) -> list[AiChunk]:
        if not embedding:
            return []
        distance = AiChunk.embedding.l2_distance(embedding)
        result = await self.session.execute(
            select(AiChunk).where(AiChunk.embedding.is_not(None)).order_by(distance).limit(limit)
        )
        return list(result.scalars().all())
