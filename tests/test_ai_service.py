from typing import Any
from uuid import uuid4

import pytest

from app.ai.service import AIService
from app.schemas.ai import AiChatRequest, DocumentIngestRequest
from app.services.exceptions import AIServiceError


class FakeLlmClient:
    async def chat_json(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.2,
    ) -> dict[str, Any]:
        return {
            "summary": "Высокий риск",
            "answer": "Риск объясняется готовыми метриками.",
            "reasons": [{"text": "risk_score=0.75", "source_type": "employee_metrics"}],
            "recommended_actions": [
                {"priority": "high", "action": "Проверить график", "reason": "Высокий risk_score"}
            ],
            "missing_data": [],
            "used_context": ["employee_metrics"],
        }


class InvalidLlmClient:
    async def chat_json(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.2,
    ) -> dict[str, Any]:
        return {"summary": "missing fields"}


class FakeContext:
    async def get_employee_context(self, employee_id: object) -> dict[str, object]:
        return {"employee_metrics": {"risk_score": 0.75, "risk_level": "high"}}

    async def get_team_context(self, team_id: object) -> dict[str, object]:
        return {"team": {"id": str(team_id)}}


class FakeRag:
    async def build_rag_context(self, query: str) -> tuple[str, list[object]]:
        return ("RAG context", [])


class FakeSession:
    async def commit(self) -> None:
        pass

    async def rollback(self) -> None:
        pass


class FakeDocuments:
    def __init__(self) -> None:
        self.by_hash: dict[str, object] = {}

    async def get_by_hash(self, content_hash: str) -> object | None:
        return self.by_hash.get(content_hash)

    async def create(self, document: object) -> object:
        if getattr(document, "id", None) is None:
            document.id = uuid4()
        self.by_hash[document.content_hash] = document
        return document


class FakeChunks:
    def __init__(self) -> None:
        self.created: list[object] = []

    async def create_many(self, chunks: list[object]) -> list[object]:
        self.created.extend(chunks)
        return chunks


@pytest.mark.asyncio
async def test_ai_service_validates_chat_response() -> None:
    service = AIService(FakeSession(), llm_client=FakeLlmClient())  # type: ignore[arg-type]
    service.context = FakeContext()  # type: ignore[assignment]
    service.rag = FakeRag()  # type: ignore[assignment]

    response = await service.chat(
        AiChatRequest(question="Почему риск высокий?", employee_id=uuid4())
    )

    assert response.summary == "Высокий риск"
    assert response.recommended_actions[0].priority == "high"


@pytest.mark.asyncio
async def test_ai_service_rejects_invalid_llm_json() -> None:
    service = AIService(FakeSession(), llm_client=InvalidLlmClient())  # type: ignore[arg-type]
    service.context = FakeContext()  # type: ignore[assignment]
    service.rag = FakeRag()  # type: ignore[assignment]

    with pytest.raises(AIServiceError, match="expected JSON schema"):
        await service.chat(AiChatRequest(question="Почему риск высокий?", employee_id=uuid4()))


@pytest.mark.asyncio
async def test_document_ingestion_creates_chunks_and_deduplicates() -> None:
    service = AIService(FakeSession(), llm_client=FakeLlmClient())  # type: ignore[arg-type]
    documents = FakeDocuments()
    chunks = FakeChunks()
    service.documents = documents  # type: ignore[assignment]
    service.chunks = chunks  # type: ignore[assignment]
    payload = DocumentIngestRequest(
        title="Rules",
        source_type="system_rules",
        source_name="manual",
        content="Правило WorkTime Sync. " * 100,
    )

    first = await service.ingest_document(payload)
    second = await service.ingest_document(payload)

    assert first.chunks_created > 0
    assert second.document_id == first.document_id
    assert second.chunks_created == 0
    assert len(chunks.created) == first.chunks_created
