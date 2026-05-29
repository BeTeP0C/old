from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CurrentEmployeeDep, get_db_session
from app.core.security import decode_access_token
from app.schemas.common import ErrorResponse
from app.schemas.notification import NotificationResponse
from app.services.exceptions import InvalidOperationError, NotFoundError
from app.services.notification_hub import hub
from app.services.notifications import NotificationService

router = APIRouter(prefix="/notifications", tags=["notifications"])
SessionDep = Annotated[AsyncSession, Depends(get_db_session)]

# Код закрытия WS «policy violation» (RFC 6455) — используем при невалидном токене.
_WS_POLICY_VIOLATION = 1008


@router.websocket("/ws")
async def notifications_ws(websocket: WebSocket, token: str = Query(...)) -> None:
    """Realtime-канал уведомлений. Аутентификация — access-токен в query (?token=).

    Сервер шлёт сигналы `{"type": "notifications:refresh"}`; клиент по ним
    перезапрашивает список через GET /notifications. Входящие сообщения клиента
    игнорируются (нужны только для keepalive/detection отключения).
    """
    try:
        payload = decode_access_token(token)
    except ValueError:
        await websocket.close(code=_WS_POLICY_VIOLATION)
        return

    employee_id = payload.employee_id
    await websocket.accept()
    await hub.register(employee_id, websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await hub.unregister(employee_id, websocket)

error_responses: dict[int | str, dict[str, Any]] = {
    status.HTTP_400_BAD_REQUEST: {"model": ErrorResponse},
    status.HTTP_403_FORBIDDEN: {"model": ErrorResponse},
    status.HTTP_404_NOT_FOUND: {"model": ErrorResponse},
}


@router.get("", response_model=list[NotificationResponse])
async def list_notifications(
    session: SessionDep,
    current: CurrentEmployeeDep,
    unread_only: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> list[NotificationResponse]:
    notifications = await NotificationService(session).list_for_recipient(
        current.id, unread_only=unread_only, limit=limit, offset=offset
    )
    return [NotificationResponse.model_validate(n) for n in notifications]


@router.patch(
    "/{notification_id}/read",
    response_model=NotificationResponse,
    responses=error_responses,
)
async def mark_notification_as_read(
    notification_id: UUID,
    session: SessionDep,
    current: CurrentEmployeeDep,
) -> NotificationResponse:
    try:
        notification = await NotificationService(session).mark_as_read(
            notification_id, current
        )
    except NotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except InvalidOperationError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)
        ) from exc
    return NotificationResponse.model_validate(notification)
