import json
from typing import Any

import httpx

from app.core.config import Settings
from app.services.exceptions import AIServiceError


class OpenRouterClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def chat_json(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.2,
    ) -> dict[str, Any]:
        if not self.settings.openrouter_api_key:
            raise AIServiceError("AI is not configured: OPENROUTER_API_KEY is missing")

        payload = {
            "model": self.settings.openrouter_model,
            "messages": messages,
            "temperature": temperature,
            "response_format": {"type": "json_object"},
        }
        headers = {
            "Authorization": f"Bearer {self.settings.openrouter_api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": self.settings.app_public_url,
            "X-OpenRouter-Title": self.settings.app_name,
        }
        url = f"{self.settings.openrouter_base_url.rstrip('/')}/chat/completions"

        try:
            async with httpx.AsyncClient(timeout=60) as client:
                response = await client.post(url, json=payload, headers=headers)
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise AIServiceError(
                f"OpenRouter returned HTTP {exc.response.status_code}"
            ) from exc
        except httpx.HTTPError as exc:
            raise AIServiceError("OpenRouter request failed") from exc

        data = response.json()
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise AIServiceError("OpenRouter response has unexpected format") from exc

        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            raise AIServiceError("OpenRouter response is not valid JSON") from exc
        if not isinstance(parsed, dict):
            raise AIServiceError("OpenRouter JSON response must be an object")
        return parsed
