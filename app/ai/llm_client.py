import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.core.config import Settings
from app.services.exceptions import AIServiceError


class OpenRouterClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def _build_payload(
        self,
        messages: list[dict[str, str]],
        temperature: float,
        *,
        stream: bool,
    ) -> dict[str, Any]:
        return {
            "model": self.settings.openrouter_model,
            "messages": messages,
            "temperature": temperature,
            "response_format": {"type": "json_object"},
            "stream": stream,
        }

    def _build_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.settings.openrouter_api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": self.settings.app_public_url,
            "X-OpenRouter-Title": self.settings.app_name,
        }

    def _build_url(self) -> str:
        return f"{self.settings.openrouter_base_url.rstrip('/')}/chat/completions"

    async def chat_json(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.2,
    ) -> dict[str, Any]:
        if not self.settings.openrouter_api_key:
            raise AIServiceError("AI is not configured: OPENROUTER_API_KEY is missing")

        try:
            async with httpx.AsyncClient(timeout=60) as client:
                response = await client.post(
                    self._build_url(),
                    json=self._build_payload(messages, temperature, stream=False),
                    headers=self._build_headers(),
                )
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

    async def chat_text_stream(
        self,
        messages: list[dict[str, str]],
        temperature: float = 0.2,
    ) -> AsyncIterator[str]:
        """Yields raw text chunks of the model's content as they arrive.

        Parses OpenRouter's SSE format (`data: {json}\\n\\n` ... `data: [DONE]`)
        and emits only the `choices[0].delta.content` strings, skipping empty
        keep-alive lines. Caller is responsible for assembling them and
        parsing the final JSON (model is asked for a JSON object via
        `response_format`).
        """
        if not self.settings.openrouter_api_key:
            raise AIServiceError("AI is not configured: OPENROUTER_API_KEY is missing")

        try:
            async with httpx.AsyncClient(timeout=None) as client:
                async with client.stream(
                    "POST",
                    self._build_url(),
                    json=self._build_payload(messages, temperature, stream=True),
                    headers=self._build_headers(),
                ) as response:
                    if response.status_code >= 400:
                        body = await response.aread()
                        raise AIServiceError(
                            f"OpenRouter returned HTTP {response.status_code}: "
                            f"{body.decode('utf-8', errors='replace')[:300]}"
                        )
                    async for line in response.aiter_lines():
                        if not line or not line.startswith("data:"):
                            continue
                        data_str = line[len("data:") :].strip()
                        if data_str == "[DONE]":
                            return
                        try:
                            event = json.loads(data_str)
                        except json.JSONDecodeError:
                            continue
                        try:
                            delta = event["choices"][0]["delta"].get("content")
                        except (KeyError, IndexError, TypeError):
                            continue
                        if delta:
                            yield delta
        except httpx.HTTPError as exc:
            raise AIServiceError("OpenRouter request failed") from exc
