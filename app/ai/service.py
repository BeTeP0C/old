import hashlib
import json
from collections.abc import AsyncIterator
from typing import Any, TypeVar
from uuid import UUID

from pydantic import BaseModel, ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.chunking import chunk_text
from app.ai.embedding_client import EmbeddingClient
from app.ai.llm_client import OpenRouterClient
from app.ai.prompts import (
    build_employee_explanation_prompt,
    build_general_rag_chat_prompt,
    build_messages,
)
from app.ai.rag import RagRetriever
from app.ai.retriever import AiContextRetriever
from app.core.config import Settings, settings
from app.models.ai_chunk import AiChunk
from app.models.ai_document import AiDocument
from app.repositories.ai_chunks import AiChunkRepository
from app.repositories.ai_documents import AiDocumentRepository
from app.schemas.ai import (
    AiChatRequest,
    AiChatResponse,
    DocumentIngestRequest,
    DocumentIngestResponse,
    EmployeeAiExplanationResponse,
)
from app.services.exceptions import AIServiceError, InvalidOperationError, NotFoundError

ResponseModelT = TypeVar("ResponseModelT", bound=BaseModel)


class AIService:
    def __init__(
        self,
        session: AsyncSession,
        llm_client: OpenRouterClient | None = None,
        app_settings: Settings | None = None,
    ) -> None:
        self.session = session
        self.settings = app_settings or settings
        self.context = AiContextRetriever(session)
        self.rag = RagRetriever(session, self.settings)
        self.documents = AiDocumentRepository(session)
        self.chunks = AiChunkRepository(session)
        self.embedding_client = EmbeddingClient(self.settings)
        self.llm_client = llm_client or OpenRouterClient(self.settings)

    async def chat(self, payload: AiChatRequest) -> AiChatResponse:
        context = await self._build_sql_context(payload)
        rag_context = ""
        if payload.use_rag:
            rag_context, _chunks = await self.rag.build_rag_context(payload.question)
        prompt = build_general_rag_chat_prompt(payload.question, context, rag_context)
        raw_response = await self.llm_client.chat_json(build_messages(prompt))
        return self._validate_ai_response(raw_response, AiChatResponse)

    async def chat_stream(self, payload: AiChatRequest) -> AsyncIterator[dict[str, Any]]:
        """Streams partial deltas of the model output, then a final validated event.

        Event shape (each yielded dict represents one SSE-event):
          {"event": "delta", "data": {"text": "..."}}        — raw text chunk
          {"event": "done",  "data": {"response": {...}}}    — parsed AiChatResponse
          {"event": "error", "data": {"detail": "..."}}      — error
        """
        context = await self._build_sql_context(payload)
        rag_context = ""
        if payload.use_rag:
            rag_context, _chunks = await self.rag.build_rag_context(payload.question)
        prompt = build_general_rag_chat_prompt(payload.question, context, rag_context)

        buffer_parts: list[str] = []
        try:
            async for chunk in self.llm_client.chat_text_stream(build_messages(prompt)):
                buffer_parts.append(chunk)
                yield {"event": "delta", "data": {"text": chunk}}
        except AIServiceError as exc:
            yield {"event": "error", "data": {"detail": str(exc)}}
            return

        raw_text = "".join(buffer_parts).strip()
        if not raw_text:
            yield {"event": "error", "data": {"detail": "AI returned empty response"}}
            return
        try:
            parsed = json.loads(raw_text)
        except json.JSONDecodeError:
            yield {
                "event": "error",
                "data": {"detail": "AI response is not valid JSON"},
            }
            return
        if not isinstance(parsed, dict):
            yield {
                "event": "error",
                "data": {"detail": "AI response must be a JSON object"},
            }
            return
        try:
            validated = AiChatResponse.model_validate(parsed)
        except ValidationError:
            yield {
                "event": "error",
                "data": {"detail": "AI response does not match expected JSON schema"},
            }
            return
        yield {"event": "done", "data": {"response": validated.model_dump(mode="json")}}

    async def explain_employee(
        self,
        employee_id: UUID,
        use_rag: bool = True,
    ) -> EmployeeAiExplanationResponse:
        context = await self.context.get_employee_context(employee_id)
        rag_context = ""
        if use_rag:
            rag_context, _chunks = await self.rag.build_rag_context(
                "как объяснять риск и рекомендации сотрудника в WorkTime Sync"
            )
        prompt = build_employee_explanation_prompt(context, rag_context)
        raw_response = await self.llm_client.chat_json(build_messages(prompt))
        if "risk_level" not in raw_response and context.get("employee_metrics"):
            raw_response["risk_level"] = context["employee_metrics"].get("risk_level")
        return self._validate_ai_response(raw_response, EmployeeAiExplanationResponse)

    async def ingest_document(self, payload: DocumentIngestRequest) -> DocumentIngestResponse:
        content_hash = hashlib.sha256(payload.content.encode("utf-8")).hexdigest()
        existing = await self.documents.get_by_hash(content_hash)
        if existing is not None:
            return DocumentIngestResponse(document_id=existing.id, chunks_created=0)

        chunks = chunk_text(payload.content)
        if not chunks:
            raise InvalidOperationError("document content is empty")

        document = AiDocument(
            title=payload.title,
            source_type=payload.source_type,
            source_name=payload.source_name,
            content_hash=content_hash,
        )
        try:
            document = await self.documents.create(document)
            chunk_models = [
                AiChunk(
                    document_id=document.id,
                    chunk_index=index,
                    content=content,
                    embedding=await self._embed_optional(content),
                )
                for index, content in enumerate(chunks)
            ]
            await self.chunks.create_many(chunk_models)
            await self.session.commit()
        except SQLAlchemyError as exc:
            await self.session.rollback()
            raise AIServiceError("failed to ingest AI document") from exc

        return DocumentIngestResponse(document_id=document.id, chunks_created=len(chunks))

    async def search_documents(self, query: str, limit: int) -> list[AiChunk]:
        return await self.rag.search_chunks(query, limit)

    async def _build_sql_context(self, payload: AiChatRequest) -> dict[str, Any]:
        if not payload.employee_id and not payload.team_id:
            return await self.context.get_overview_context()
        context: dict[str, Any] = {}
        if payload.employee_id:
            context["employee_context"] = await self.context.get_employee_context(
                payload.employee_id
            )
        if payload.team_id:
            context["team_context"] = await self.context.get_team_context(payload.team_id)
        return context

    async def _embed_optional(self, text: str) -> list[float] | None:
        if not self.settings.embeddings_enabled:
            return None
        try:
            return await self.embedding_client.embed_text(text)
        except (NotImplementedError, RuntimeError, ValueError):
            return None

    def _validate_ai_response(
        self,
        data: dict[str, Any],
        schema: type[ResponseModelT],
    ) -> ResponseModelT:
        try:
            return schema.model_validate(data)
        except ValidationError as exc:
            raise AIServiceError("AI response does not match expected JSON schema") from exc


__all__ = ("AIService", "AIServiceError", "InvalidOperationError", "NotFoundError")
