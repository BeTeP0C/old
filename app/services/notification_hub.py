"""In-process реестр WebSocket-подключений для realtime-уведомлений.

Один uvicorn-процесс (dev/один контейнер) → достаточно in-memory словаря
`employee_id -> {WebSocket}`. При нескольких воркерах понадобился бы общий
шина (Redis pub/sub) — это отмечено как ограничение.

Модель доставки — «сигнал, а не payload»: сервер шлёт клиенту короткое
`{"type": "notifications:refresh"}`, а клиент перезапрашивает список из БД.
Так источником истины остаётся БД, и нет рассинхрона/проблем сериализации.
"""

from __future__ import annotations

import asyncio
import logging
from uuid import UUID

from fastapi import WebSocket

logger = logging.getLogger(__name__)

_REFRESH_MESSAGE = {"type": "notifications:refresh"}


class NotificationHub:
    def __init__(self) -> None:
        self._connections: dict[UUID, set[WebSocket]] = {}
        self._lock = asyncio.Lock()

    async def register(self, employee_id: UUID, websocket: WebSocket) -> None:
        async with self._lock:
            self._connections.setdefault(employee_id, set()).add(websocket)

    async def unregister(self, employee_id: UUID, websocket: WebSocket) -> None:
        async with self._lock:
            connections = self._connections.get(employee_id)
            if connections is not None:
                connections.discard(websocket)
                if not connections:
                    self._connections.pop(employee_id, None)

    async def signal(self, employee_id: UUID) -> None:
        """Просит всех клиентов сотрудника перезапросить уведомления.

        Best-effort: отвалившиеся сокеты молча удаляются, исключения не
        пробрасываются наружу (доставка realtime не должна ломать бизнес-операцию).
        """
        async with self._lock:
            targets = list(self._connections.get(employee_id, ()))
        for websocket in targets:
            try:
                await websocket.send_json(_REFRESH_MESSAGE)
            except Exception:  # noqa: BLE001 — сокет мог закрыться в любой момент
                await self.unregister(employee_id, websocket)


# Процессный синглтон — импортируется и WS-эндпоинтом, и сервисами.
hub = NotificationHub()
