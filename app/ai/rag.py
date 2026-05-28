from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.embedding_client import EmbeddingClient
from app.core.config import Settings
from app.models.ai_chunk import AiChunk
from app.repositories.ai_chunks import AiChunkRepository


class RagRetriever:
    def __init__(self, session: AsyncSession, settings: Settings) -> None:
        self.chunks = AiChunkRepository(session)
        self.embedding_client = EmbeddingClient(settings)
        self.settings = settings

    async def search_chunks(self, query: str, limit: int = 5) -> list[AiChunk]:
        if self.settings.embeddings_enabled:
            try:
                embedding = await self.embedding_client.embed_text(query)
                return await self.chunks.search_by_embedding(embedding, limit)
            except (AttributeError, NotImplementedError, RuntimeError, ValueError):
                pass
        return await self.chunks.search_by_text(query, limit)

    async def build_rag_context(self, query: str, limit: int = 5) -> tuple[str, list[AiChunk]]:
        chunks = await self.search_chunks(query, limit)
        return build_rag_context(chunks), chunks


def build_rag_context(chunks: list[AiChunk]) -> str:
    if not chunks:
        return ""
    parts = []
    for chunk in chunks:
        parts.append(
            f"[rag_chunk:{chunk.id}; document_id:{chunk.document_id}; index:{chunk.chunk_index}]\n"
            f"{chunk.content}"
        )
    return "\n\n".join(parts)
