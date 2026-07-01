import json
from typing import Any, Protocol

import httpx
from anthropic import AsyncAnthropic

from app.config import Settings


class LLMError(RuntimeError):
    pass


class JSONClient(Protocol):
    async def generate(self, system: str, payload: dict[str, Any]) -> Any: ...


class ClaudeJSONClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = (
            AsyncAnthropic(api_key=settings.anthropic_api_key)
            if settings.anthropic_api_key
            else None
        )

    async def generate(self, system: str, payload: dict[str, Any]) -> Any:
        if not self.client:
            raise LLMError("ANTHROPIC_API_KEY is not configured")
        response = await self.client.messages.create(
            model=self.settings.anthropic_model,
            max_tokens=2500,
            temperature=0.2,
            system=system,
            messages=[{"role": "user", "content": json.dumps(payload, default=str)}],
        )
        text = "".join(block.text for block in response.content if block.type == "text").strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0]
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise LLMError("Claude returned invalid JSON") from exc


class GroqJSONClient:
    """OpenAI-compatible Groq client using JSON object mode."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def generate(self, system: str, payload: dict[str, Any]) -> Any:
        if not self.settings.groq_api_key:
            raise LLMError("GROQ_API_KEY is not configured")
        request = {
            "model": self.settings.groq_model,
            "temperature": 0.2,
            "max_completion_tokens": 2500,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "system",
                    "content": (
                        f"{system}\nAlways return one valid JSON value. "
                        "Only when the entire requested response is a top-level array, "
                        'wrap that array once as {"items": [...]}. Arrays nested inside '
                        "an object must remain ordinary JSON arrays."
                    ),
                },
                {"role": "user", "content": json.dumps(payload, default=str)},
            ],
        }
        try:
            async with httpx.AsyncClient(timeout=45) as client:
                response = await client.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.settings.groq_api_key}",
                        "Content-Type": "application/json",
                    },
                    json=request,
                )
                response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            result = json.loads(content)
            return result.get("items", result) if isinstance(result, dict) else result
        except (httpx.HTTPError, KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise LLMError(f"Groq request failed: {exc}") from exc


def create_json_client(settings: Settings) -> JSONClient:
    provider = settings.llm_provider.strip().lower()
    if provider == "groq":
        return GroqJSONClient(settings)
    if provider == "anthropic":
        return ClaudeJSONClient(settings)
    raise ValueError("LLM_PROVIDER must be 'groq' or 'anthropic'")
