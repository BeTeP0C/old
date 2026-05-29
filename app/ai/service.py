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
from app.ai.retriever import AiContextRetriever, is_scheduling_question
from app.core.config import Settings, settings
from app.models.ai_chunk import AiChunk
from app.models.ai_document import AiDocument
from app.repositories.ai_chunks import AiChunkRepository
from app.repositories.ai_documents import AiDocumentRepository
from app.schemas.ai import (
    AiAction,
    AiChatRequest,
    AiChatResponse,
    AiEntity,
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
        response = self._validate_ai_response(raw_response, AiChatResponse)
        return self._enrich_response(response, context, payload)

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
        validated = self._enrich_response(validated, context, payload)
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
            return await self.context.get_overview_context(question=payload.question)
        context: dict[str, Any] = {}
        if payload.employee_id:
            context["employee_context"] = await self.context.get_employee_context(
                payload.employee_id,
                include_availability=True,
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

    def _enrich_response(
        self,
        response: AiChatResponse,
        context: dict[str, Any],
        payload: AiChatRequest,
    ) -> AiChatResponse:
        """Детерминированно добавляет кликабельные сущности и действия-переходы.

        Сущности/действия НЕ берутся у модели (она ненадёжна с id) — мы сами
        сопоставляем имена сотрудников/команд из контекста с текстом ответа.
        """
        employees, teams = _index_people_and_teams(context)
        text = f"{response.summary}\n{response.answer}".lower()
        scheduling = is_scheduling_question(payload.question)

        employee_ids: list[str] = [
            eid for eid, name in employees.items() if _name_in_text(name, text)
        ]
        scoped_employee = str(payload.employee_id) if payload.employee_id else None
        if scoped_employee and scoped_employee in employees and scoped_employee not in employee_ids:
            employee_ids.append(scoped_employee)

        scoped_team = str(payload.team_id) if payload.team_id else None
        team_ids: list[str] = [
            tid
            for tid, name in teams.items()
            if tid == scoped_team or _team_in_text(name, text)
        ]

        entities: list[AiEntity] = [
            AiEntity(type="employee", id=UUID(eid), label=employees[eid]) for eid in employee_ids
        ]
        entities += [AiEntity(type="team", id=UUID(tid), label=teams[tid]) for tid in team_ids]

        actions: list[AiAction] = []
        if scheduling:
            meeting_team_ids = team_ids or (list(teams) if len(teams) == 1 else [])
            for tid in meeting_team_ids:
                actions.append(
                    AiAction(
                        type="open_team_meeting",
                        label=f"Подобрать время встречи · {teams[tid]}",
                        team_id=UUID(tid),
                    )
                )
        if response.recommended_actions:
            actions.append(
                AiAction(type="open_recommendations", label="Открыть все рекомендации")
            )

        return response.model_copy(update={"entities": entities, "actions": actions})


def _index_people_and_teams(
    context: dict[str, Any],
) -> tuple[dict[str, str], dict[str, str]]:
    """Собирает id→имя сотрудников и id→название команд из любого варианта
    контекста (overview / employee_context / team_context)."""
    employees: dict[str, str] = {}
    teams: dict[str, str] = {}

    def add_employee(raw_id: Any, name: Any) -> None:
        if raw_id and isinstance(name, str) and name:
            employees[str(raw_id)] = name

    def add_team(raw_id: Any, name: Any) -> None:
        if raw_id and isinstance(name, str) and name:
            teams[str(raw_id)] = name

    for key in ("top_overloaded", "top_outdated_schedules", "top_conflicts", "top_risk"):
        for row in context.get(key, []) or []:
            add_employee(row.get("employee_id"), row.get("full_name"))
    for option in context.get("team_meeting_options", []) or []:
        add_team(option.get("team_id"), option.get("team_name"))

    employee_ctx = context.get("employee_context")
    if isinstance(employee_ctx, dict):
        employee = employee_ctx.get("employee") or {}
        add_employee(employee.get("id"), employee.get("full_name"))

    team_ctx = context.get("team_context")
    if isinstance(team_ctx, dict):
        team = team_ctx.get("team") or {}
        add_team(team.get("id"), team.get("name"))
        for member in team_ctx.get("members", []) or []:
            employee = member.get("employee") or {}
            add_employee(employee.get("id"), employee.get("full_name"))

    return employees, teams


def _name_in_text(full_name: str, low_text: str) -> bool:
    """Упоминается ли сотрудник в тексте. Матчит по фамилии (последнее слово) со
    срезом последней буквы — чтобы пережить русские склонения
    (Сидорова → «Сидоровой», «Сидорову»)."""
    if not full_name:
        return False
    if full_name.lower() in low_text:
        return True
    parts = full_name.split()
    if not parts:
        return False
    surname = parts[-1].lower()
    if len(surname) < 5:
        return False
    stem = surname[:-1]
    return stem in low_text


def _team_in_text(team_name: str, low_text: str) -> bool:
    """Упоминается ли команда в тексте. Матчит по значимым словам названия со
    срезом окончания, чтобы пережить склонение («Команда разработки» →
    «командой разработки»)."""
    if not team_name:
        return False
    if team_name.lower() in low_text:
        return True
    for token in team_name.lower().split():
        if len(token) >= 5 and token[:-1] in low_text:
            return True
    return False


__all__ = ("AIService", "AIServiceError", "InvalidOperationError", "NotFoundError")
