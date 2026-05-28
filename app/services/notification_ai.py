"""LLM-генерация title/body для smart-уведомлений.

Опциональный шаг: если `OPENROUTER_API_KEY` есть в .env, мы спрашиваем у
OpenRouter короткий персонализированный текст. Если ключа нет или вызов
упал — возвращаем None и сервис использует rule-based title/body из Draft.

Это закрывает «ИИ должен выбирать причину уведомления» (§16 п.6 ТЗ): причина
формулируется не из шаблона, а из контекста (имя получателя, его роль,
конкретные метрики, что именно изменилось).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.ai.llm_client import OpenRouterClient
from app.core.config import Settings, settings as default_settings
from app.models.employee import Employee
from app.services.exceptions import AIServiceError

if TYPE_CHECKING:
    from app.services.notifications import NotificationDraft

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GeneratedNotificationText:
    title: str
    body: str


_SYSTEM_PROMPT = (
    "Ты — система умных уведомлений WorkTime Sync. Твоя задача — "
    "сформулировать короткое, конкретное уведомление для конкретного "
    "получателя на основании контекста. Правила:\n"
    "1. Текст на русском, в деловом тоне, без эмодзи.\n"
    "2. Title — до 80 символов, без точки в конце.\n"
    "3. Body — 1-2 предложения, до 240 символов, с конкретными числами из контекста.\n"
    "4. Не выдумывай факты, не упомянутые в контексте.\n"
    "5. Учитывай роль получателя: руководителю — «у вашего сотрудника X»,"
    " самому сотруднику — «ваш график»."
)

_JSON_INSTRUCTIONS = (
    'Верни JSON: {"title": "...", "body": "..."}'
)


class NotificationAIGenerator:
    """Тонкая обёртка над OpenRouter с graceful fallback."""

    def __init__(
        self,
        *,
        llm_client: OpenRouterClient | None = None,
        app_settings: Settings | None = None,
    ) -> None:
        self.settings = app_settings or default_settings
        self.llm_client = llm_client
        self._enabled = bool(self.settings.openrouter_api_key)

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def generate(
        self,
        *,
        draft: NotificationDraft,
        recipient: Employee,
    ) -> GeneratedNotificationText | None:
        """Возвращает сгенерированный текст или None при недоступности/ошибке."""
        if not self._enabled:
            return None
        client = self.llm_client or OpenRouterClient(self.settings)
        user_prompt = _build_user_prompt(draft=draft, recipient=recipient)
        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": f"{user_prompt}\n\n{_JSON_INSTRUCTIONS}"},
        ]
        try:
            response = await client.chat_json(messages, temperature=0.3)
        except AIServiceError as exc:
            logger.warning("notification AI generation failed: %s", exc)
            return None

        title = _clean(response.get("title"), max_len=120)
        body = _clean(response.get("body"), max_len=400)
        if not title or not body:
            logger.warning("notification AI returned empty title/body: %s", response)
            return None
        return GeneratedNotificationText(title=title, body=body)


def _clean(value: object, *, max_len: int) -> str:
    if not isinstance(value, str):
        return ""
    cleaned = value.strip().replace("\n", " ")
    return cleaned[:max_len]


def _build_user_prompt(*, draft: NotificationDraft, recipient: Employee) -> str:
    context = {
        "recipient": {
            "full_name": recipient.full_name,
            "role": str(recipient.role),
            "timezone": recipient.timezone,
        },
        "draft": {
            "type": draft.type,
            "severity": draft.severity,
            "fallback_title": draft.title,
            "fallback_body": draft.body,
            "subject_type": draft.subject_type,
            "subject_id": str(draft.subject_id),
            "payload": draft.payload,
        },
    }
    return (
        "Сгенерируй уведомление для получателя на основании контекста.\n"
        "Контекст (JSON):\n"
        f"{json.dumps(context, ensure_ascii=False, indent=2)}"
    )
