import pytest

from app.ai.rag import RagRetriever
from app.core.config import settings


class FakeChunkRepository:
    def __init__(self) -> None:
        self.used_text_search = False

    async def search_by_text(self, query: str, limit: int) -> list[str]:
        self.used_text_search = True
        return ["chunk"] if "risk" in query else []

    async def search_by_embedding(self, embedding: list[float], limit: int) -> list[str]:
        return []


@pytest.mark.asyncio
async def test_rag_search_falls_back_to_text_search() -> None:
    retriever = RagRetriever(object(), settings)  # type: ignore[arg-type]
    fake_chunks = FakeChunkRepository()
    retriever.chunks = fake_chunks  # type: ignore[assignment]

    result = await retriever.search_chunks("risk", limit=5)

    assert result == ["chunk"]
    assert fake_chunks.used_text_search is True
