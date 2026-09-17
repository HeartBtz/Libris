"""Anthropic Claude API provider implementation."""

import logging
from typing import TypeVar

import httpx
from pydantic import BaseModel

from app.models import Provider
from app.providers.llm import estimate_tokens, json_schema, parse_json
from app.security import decrypt

T = TypeVar("T", bound=BaseModel)
logger = logging.getLogger("epub.anthropic")


class AnthropicProvider:
    """Direct Anthropic API provider for Claude models."""

    async def models(self, provider: Provider) -> list[str]:
        """List available Claude models."""
        # Anthropic doesn't provide a models endpoint, return known models
        return [
            "claude-3-5-sonnet-20241022",
            "claude-3-5-haiku-20241022",
            "claude-3-opus-20240229",
            "claude-3-sonnet-20240229",
            "claude-3-haiku-20240307",
        ]

    @staticmethod
    def headers(provider: Provider) -> dict:
        """Build Anthropic API headers."""
        key = decrypt(provider.encrypted_key)
        return {
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

    async def complete(
        self,
        *,
        provider: Provider,
        messages: list[dict],
        response_model: type[T],
        temperature: float | None = None,
    ) -> T:
        """
        Complete a request using Anthropic Messages API.

        Args:
            provider: Provider configuration
            messages: OpenAI-style messages (system + user/assistant)
            response_model: Pydantic model for structured output
            temperature: Optional temperature override

        Returns:
            Parsed response matching response_model
        """
        schema = json_schema(response_model)

        # Convert OpenAI format to Anthropic format
        system_message = None
        anthropic_messages = []

        for msg in messages:
            if msg["role"] == "system":
                system_message = msg["content"]
            else:
                anthropic_messages.append(
                    {"role": msg["role"], "content": msg["content"]}
                )

        # Add schema instruction at the end
        if anthropic_messages:
            last_msg = anthropic_messages[-1]
            if last_msg["role"] == "user":
                last_msg["content"] += (
                    f"\n\nReturn valid JSON matching this schema:\n{schema}"
                )
            else:
                anthropic_messages.append(
                    {
                        "role": "user",
                        "content": f"Return valid JSON matching this schema:\n{schema}",
                    }
                )

        # Estimate tokens for logging
        total_tokens = sum(estimate_tokens(str(m)) for m in anthropic_messages)
        if system_message:
            total_tokens += estimate_tokens(system_message)

        # Build request payload
        payload = {
            "model": provider.model,
            "max_tokens": min(provider.max_output_tokens, 8192),
            "messages": anthropic_messages,
        }

        if system_message:
            payload["system"] = system_message

        if temperature is not None:
            payload["temperature"] = temperature
        elif provider.temperature is not None:
            payload["temperature"] = provider.temperature

        logger.debug(
            f"Anthropic request: model={provider.model} tokens~{total_tokens} "
            f"max_tokens={payload['max_tokens']}"
        )

        async with httpx.AsyncClient(
            timeout=provider.timeout, follow_redirects=False
        ) as client:
            response = await client.post(
                f"{provider.base_url}/v1/messages",
                headers=self.headers(provider),
                json=payload,
            )

            response.raise_for_status()
            data = response.json()

            # Extract text from content blocks
            content = ""
            for block in data.get("content", []):
                if block.get("type") == "text":
                    content += block.get("text", "")

            if not content:
                raise ValueError("Anthropic response contained no text content")

            logger.debug(f"Anthropic response: {len(content)} chars")

            # Parse JSON from response
            return parse_json(content, response_model)
