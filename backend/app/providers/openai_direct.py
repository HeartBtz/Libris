"""Direct OpenAI API provider implementation."""

import logging
from typing import TypeVar

import httpx
from pydantic import BaseModel

from app.models import Provider
from app.providers.llm import estimate_tokens, json_schema, parse_json
from app.security import decrypt

T = TypeVar("T", bound=BaseModel)
logger = logging.getLogger("epub.openai")


class OpenAIDirectProvider:
    """Direct OpenAI API provider for GPT models."""

    async def models(self, provider: Provider) -> list[str]:
        """List available OpenAI models."""
        async with httpx.AsyncClient(
            timeout=provider.timeout, follow_redirects=False
        ) as client:
            response = await client.get(
                f"{provider.base_url}/v1/models",
                headers=self.headers(provider),
            )
            response.raise_for_status()
            data = response.json()
            return [m["id"] for m in data.get("data", []) if "id" in m]

    @staticmethod
    def headers(provider: Provider) -> dict:
        """Build OpenAI API headers."""
        key = decrypt(provider.encrypted_key)
        return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    async def complete(
        self,
        *,
        provider: Provider,
        messages: list[dict],
        response_model: type[T],
        temperature: float | None = None,
    ) -> T:
        """
        Complete a request using OpenAI Chat Completions API.

        Args:
            provider: Provider configuration
            messages: OpenAI-style messages
            response_model: Pydantic model for structured output
            temperature: Optional temperature override

        Returns:
            Parsed response matching response_model
        """
        schema = json_schema(response_model)

        # Add schema instruction
        enhanced_messages = [
            *messages,
            {
                "role": "system",
                "content": f"Return valid JSON matching this schema:\n{schema}",
            },
        ]

        # Estimate tokens
        total_tokens = sum(estimate_tokens(str(m)) for m in enhanced_messages)

        # Build request payload with structured output if supported
        caps = provider.capabilities
        supports_structured = caps.get("supports_json_schema", False)

        payload = {
            "model": provider.model,
            "messages": enhanced_messages,
            "max_tokens": provider.max_output_tokens,
        }

        if temperature is not None:
            payload["temperature"] = temperature
        elif provider.temperature is not None:
            payload["temperature"] = provider.temperature

        if supports_structured:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "response", "schema": schema, "strict": True},
            }
        else:
            payload["response_format"] = {"type": "json_object"}

        logger.debug(
            f"OpenAI request: model={provider.model} tokens~{total_tokens} "
            f"structured={supports_structured}"
        )

        async with httpx.AsyncClient(
            timeout=provider.timeout, follow_redirects=False
        ) as client:
            response = await client.post(
                f"{provider.base_url}/v1/chat/completions",
                headers=self.headers(provider),
                json=payload,
            )

            response.raise_for_status()
            data = response.json()

            content = data["choices"][0]["message"]["content"]

            if not content:
                raise ValueError("OpenAI response contained no content")

            logger.debug(f"OpenAI response: {len(content)} chars")

            return parse_json(content, response_model)
