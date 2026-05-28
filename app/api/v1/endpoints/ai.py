from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.service import AIService
from app.api.deps import CurrentEmployeeDep, get_db_session
from app.schemas.ai import (
    AiChatRequest,
    AiChatResponse,
    AiChunkResponse,
    DocumentIngestRequest,
    DocumentIngestResponse,
    EmployeeAiExplanationRequest,
    EmployeeAiExplanationResponse,
)
from app.schemas.common import ErrorResponse
from app.services.exceptions import AIServiceError, InvalidOperationError, NotFoundError

router = APIRouter(prefix="/ai", tags=["ai"])
SessionDep = Annotated[AsyncSession, Depends(get_db_session)]

error_responses: dict[int | str, dict[str, Any]] = {
    status.HTTP_400_BAD_REQUEST: {"model": ErrorResponse},
    status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
    status.HTTP_502_BAD_GATEWAY: {"model": ErrorResponse},
    status.HTTP_503_SERVICE_UNAVAILABLE: {"model": ErrorResponse},
}


@router.post("/chat", response_model=AiChatResponse, responses=error_responses)
async def chat(
    payload: AiChatRequest,
    session: SessionDep,
    _current_employee: CurrentEmployeeDep,
) -> AiChatResponse:
    try:
        return await AIService(session).chat(payload)
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except AIServiceError as exc:
        raise _ai_http_exception(exc) from exc


@router.post(
    "/employees/{employee_id}/explain",
    response_model=EmployeeAiExplanationResponse,
    responses=error_responses,
)
async def explain_employee(
    employee_id: UUID,
    payload: EmployeeAiExplanationRequest,
    session: SessionDep,
    _current_employee: CurrentEmployeeDep,
) -> EmployeeAiExplanationResponse:
    try:
        return await AIService(session).explain_employee(employee_id, payload.use_rag)
    except NotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except AIServiceError as exc:
        raise _ai_http_exception(exc) from exc


@router.post(
    "/documents",
    response_model=DocumentIngestResponse,
    status_code=status.HTTP_201_CREATED,
    responses=error_responses,
)
async def ingest_document(
    payload: DocumentIngestRequest,
    session: SessionDep,
    _current_employee: CurrentEmployeeDep,
) -> DocumentIngestResponse:
    try:
        return await AIService(session).ingest_document(payload)
    except InvalidOperationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except AIServiceError as exc:
        raise _ai_http_exception(exc) from exc


@router.get(
    "/documents/search",
    response_model=list[AiChunkResponse],
    responses=error_responses,
)
async def search_documents(
    session: SessionDep,
    _current_employee: CurrentEmployeeDep,
    query: str = Query(min_length=1),
    limit: int = Query(default=5, ge=1, le=20),
) -> list[AiChunkResponse]:
    chunks = await AIService(session).search_documents(query, limit)
    return [AiChunkResponse.model_validate(chunk) for chunk in chunks]


def _ai_http_exception(exc: AIServiceError) -> HTTPException:
    detail = str(exc)
    if "OPENROUTER_API_KEY" in detail:
        return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=detail)
    return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=detail)
