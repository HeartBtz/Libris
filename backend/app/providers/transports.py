"""Wire formats only; cache, validation, retries and tracing remain in llm.py."""

from app.models import Provider


def generation_parameters(provider: Provider, temperature: float | None) -> dict:
    params = {"model": provider.model}
    effort = provider.capabilities.get("reasoning_effort")
    if provider.kind == "openai":
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
    if kind == "openai":
        return raw
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
