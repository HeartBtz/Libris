"""Wire formats only; cache, validation, retries and tracing remain in llm.py."""

from app.models import Provider

NATIVE = ("anthropic", "openai_direct")


def endpoint(provider: Provider, resource: str = "") -> str:
    """Request URL. Native kinds accept the vendor root or a base already ending in /v1."""
    if provider.kind in NATIVE:
        root = provider.base_url.rstrip("/").removesuffix("/v1") + "/v1"
        return root + (resource or ("/messages" if provider.kind == "anthropic" else "/chat/completions"))
    return provider.base_url + (
        resource or ("/responses" if provider.kind == "openai_responses" else "/chat/completions")
    )


def generation_parameters(provider: Provider, temperature: float | None) -> dict:
    params = {"model": provider.model}
    effort = (
        provider.capabilities.get("reasoning_effort")
        if provider.capabilities.get("supports_reasoning")
        else ""
    )
    if provider.kind == "anthropic":
        # Current Claude models reject sampling parameters (HTTP 400): none is sent.
        params.update(max_tokens=provider.max_output_tokens)
    elif provider.kind in ("openai", "openai_direct"):
        params.update(
            temperature=provider.temperature if temperature is None else temperature, top_p=provider.top_p
        )
        params[provider.capabilities.get("max_tokens_parameter", "max_tokens")] = provider.max_output_tokens
        if provider.capabilities.get("supports_reasoning") and effort:
            params["reasoning_effort"] = effort
    elif provider.kind == "openai_responses":
        params.update(max_output_tokens=provider.max_output_tokens, store=False)
        if effort:
            params["reasoning"] = {"effort": effort}
    else:
        params.update(
            timeout=provider.timeout,
            output_reservation=provider.max_output_tokens,
            context_window=provider.context_window,
        )
        if effort:
            params["effort"] = effort
    return params


def wire_payload(kind: str, params: dict, messages: list, schema: dict, name: str, mode: str) -> dict:
    if kind == "codex_chatgpt":
        return dict(params, messages=messages, schema=schema)
    if kind == "openai_responses":
        payload = dict(params, input=messages, tools=[], tool_choice="none")
        if mode != "text":
            fmt = (
                {"type": "json_object"}
                if mode == "json_object"
                else {"type": "json_schema", "name": name, "strict": True, "schema": schema}
            )
            payload["text"] = {"format": fmt}
        return payload
    if kind == "anthropic":
        # The Messages API takes one top-level system prompt; every system message counts.
        system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
        turns = [{"role": m["role"], "content": m["content"]} for m in messages if m["role"] != "system"]
        if not turns:
            turns, system = [{"role": "user", "content": system}], ""
        return dict(params, messages=turns, **({"system": system} if system else {}))
    payload = dict(params, messages=messages)
    if mode == "json_schema":
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": name, "strict": True, "schema": schema},
        }
    elif mode == "json_object":
        payload["response_format"] = {"type": "json_object"}
    return payload


def normalize_response(kind: str, raw: dict) -> dict:
    if kind in ("openai", "openai_direct"):
        return raw
    if kind == "anthropic":
        content = "".join(b.get("text", "") for b in raw.get("content", []) if b.get("type") == "text")
        stop = raw.get("stop_reason")
        refusal = "refused" if stop == "refusal" else None
        finish = (
            "stop"
            if stop in ("end_turn", "stop_sequence")
            else "content_filter"
            if stop == "refusal"
            else "length"
            if stop in ("max_tokens", "model_context_window_exceeded")
            else str(stop or "error")
        )
        usage = raw.get("usage") or {}
        return {
            "choices": [{"finish_reason": finish, "message": {"content": content, "refusal": refusal}}],
            "usage": {
                "prompt_tokens": usage.get("input_tokens", 0)
                + usage.get("cache_creation_input_tokens", 0)
                + usage.get("cache_read_input_tokens", 0),
                "completion_tokens": usage.get("output_tokens", 0),
            },
            "provider_response": raw,
        }
    if kind == "codex_chatgpt":
        content = raw.get("text", "")
        finish = "stop" if raw.get("status") == "completed" else "error"
        refusal = None
    else:
        texts, refusals = [], []
        for item in raw.get("output", []):
            if item.get("type") != "message" or item.get("role") != "assistant":
                continue
            for part in item.get("content", []):
                if part.get("type") == "output_text":
                    texts.append(part.get("text", ""))
                elif part.get("type") == "refusal":
                    refusals.append(part.get("refusal", "refused"))
        content, refusal = "".join(texts), " ".join(refusals) or None
        finish = (
            "stop"
            if raw.get("status") == "completed"
            else "length"
            if raw.get("status") == "incomplete"
            else "error"
        )
    usage = raw.get("usage") or {}
    return {
        "choices": [{"finish_reason": finish, "message": {"content": content, "refusal": refusal}}],
        "usage": {
            "prompt_tokens": usage.get("input_tokens", 0),
            "completion_tokens": usage.get("output_tokens", 0),
        },
        "provider_response": raw,
    }
