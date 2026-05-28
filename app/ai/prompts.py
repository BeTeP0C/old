import json
from typing import Any

SYSTEM_PROMPT = """
Ты AI-ассистент системы WorkTime Sync.

Твоя задача:
- объяснять рабочие графики, конфликты, перегрузку, доступность и риски;
- использовать только переданный контекст;
- не выдумывать сотрудников, события, метрики и рекомендации;
- не пересчитывать метрики, если они уже переданы;
- если данных не хватает, указать это в missing_data;
- отвечать на русском языке;
- возвращать строго JSON.

Правила:
1. employee_metrics является source of truth для risk_score, risk_level,
   actuality_score, conflict_rate и load_level.
2. Не изменяй числовые метрики.
3. Rule-based рекомендации являются основанием для AI-объяснений.
4. Не утверждай, что подключены Google Calendar, Jira или HR API, если в контексте этого нет.
5. Для каждого вывода указывай причину.
""".strip()

JSON_RESPONSE_INSTRUCTIONS = """
Верни только JSON объекта такого формата:
{
  "summary": "Краткий вывод",
  "answer": "Полный ответ простым языком",
  "reasons": [
    {
      "text": "Причина",
      "source_type": "employee_metrics | activity_events | work_schedules | rag_chunk | null",
      "source_id": "id или null"
    }
  ],
  "recommended_actions": [
    {
      "priority": "low | medium | high | critical",
      "action": "Что сделать",
      "reason": "Почему это действие нужно"
    }
  ],
  "missing_data": [],
  "used_context": ["employee_metrics", "work_schedules", "activity_events", "rag_chunks"]
}
""".strip()


def build_employee_explanation_prompt(context: dict[str, Any], rag_context: str = "") -> str:
    return _build_prompt(
        task="Объясни риск, метрики и рекомендации для сотрудника.",
        context=context,
        rag_context=rag_context,
    )


def build_team_explanation_prompt(context: dict[str, Any], rag_context: str = "") -> str:
    return _build_prompt(
        task="Объясни состояние команды, риски участников и рекомендации.",
        context=context,
        rag_context=rag_context,
    )


def build_general_rag_chat_prompt(
    question: str,
    context: dict[str, Any],
    rag_context: str = "",
) -> str:
    return _build_prompt(
        task=f"Ответь на вопрос пользователя: {question}",
        context=context,
        rag_context=rag_context,
    )


def build_messages(user_prompt: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]


def _build_prompt(task: str, context: dict[str, Any], rag_context: str) -> str:
    payload = json.dumps(context, ensure_ascii=False, default=str, indent=2)
    rag = rag_context if rag_context else "RAG-контекст не найден или не запрошен."
    return (
        f"{task}\n\n"
        f"Контекст WorkTime Sync:\n{payload}\n\n"
        f"RAG-контекст:\n{rag}\n\n"
        f"{JSON_RESPONSE_INSTRUCTIONS}"
    )
