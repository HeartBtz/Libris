import asyncio
import hashlib
import json
import logging
import math
import random
import time
from typing import TypeVar

import httpx
from pydantic import BaseModel, ValidationError
from sqlalchemy import func, select, update
from sqlalchemy.exc import SQLAlchemyError

from app.config import settings
from app.db import SessionLocal
from app.jobs.execution import execution
from app.models import Prompt, Provider, RequestLog
from app.providers.codex import bridge_call
from app.providers.refusals import refusal_http, refusal_reason
from app.providers.transports import generation_parameters, normalize_response, wire_payload
from app.security import decrypt

T = TypeVar("T", bound=BaseModel)
logger = logging.getLogger("epub.llm")


class LLMError(Exception):
    pass


class InvalidResponseExhausted(LLMError):
    """All five attempts produced unusable output, rather than a service outage."""



class ProviderUnavailable(LLMError):
    def __init__(self, message: str, retry_after: float = 0):
        super().__init__(message)
        self.retry_after = retry_after


class ProviderAuthenticationRequired(LLMError):
    pass


class ProviderContentRefused(LLMError):
    pass


def estimate_tokens(value: str) -> int:
    # Conservative byte bound, deliberately not advertised as exact model tokenization.
    return len(value.encode("utf-8")) + 16


def load_prompt(name: str, source: str, target: str) -> tuple[str, str]:
    with SessionLocal() as db:
        override = db.scalar(select(Prompt).where(Prompt.name == name).order_by(Prompt.version.desc()))
        content = override.content if override else (settings().prompt_dir / f"{name}.txt").read_text()
        version = str(override.version) if override else "file-v1"
    return content.replace("{source_language}", source).replace("{target_language}", target), version


def json_schema(model: type[BaseModel]) -> dict:
    schema = model.model_json_schema()

    def strict(node):
        if isinstance(node, dict):
            node.pop("default", None)
            if node.get("type") == "object":
                node["additionalProperties"] = False
                node["required"] = list(node.get("properties", {}))
            for value in node.values():
                strict(value)
        elif isinstance(node, list):
            for value in node:
                strict(value)

    strict(schema)
    return schema


def parse_json(content: str, model: type[T]) -> T:
    text = content.strip()
    if text.startswith("```json") and text.endswith("```"):
        text = text[7:-3].strip()
    elif text.startswith("```") and text.endswith("```"):
        text = text[3:-3].strip()
    # No slicing from first '{' to last '}': prose / reasoning contamination must be rejected.
    return model.model_validate_json(text)


class OpenAIProvider:
    async def models(self, provider: Provider) -> list[str]:
        if provider.kind == "codex_chatgpt":
            result = await bridge_call(provider.id, "models")
            return result["models"]
        if provider.kind == "anthropic":
            from app.providers.anthropic import AnthropicProvider
            return await AnthropicProvider().models(provider)
        if provider.kind == "openai_direct":
            from app.providers.openai_direct import OpenAIDirectProvider
            return await OpenAIDirectProvider().models(provider)
        async with httpx.AsyncClient(
            timeout=provider.timeout, follow_redirects=False, trust_env=False
        ) as client:
            response = await client.get(provider.base_url + "/models", headers=self.headers(provider))
            response.raise_for_status()
            return [str(m["id"]) for m in response.json().get("data", []) if "id" in m]

    @staticmethod
    def headers(provider: Provider) -> dict:
        key = decrypt(provider.encrypted_key)
        return {"Authorization": f"Bearer {key}"} if key else {}

    async def complete(
        self,
        *,
        project_id: str,
        provider_id: str,
        operation: str,
        messages: list[dict],
        response_model: type[T],
        segment_id: str | None = None,
        context: dict | None = None,
        temperature: float | None = None,
        validator=None,
    ) -> T:
        with SessionLocal() as db:
            provider = db.get(Provider, provider_id)
            if not provider:
                raise LLMError("Provider non configuré.")
        schema = json_schema(response_model)
        context = {
            **(context or {}),
            "transport": provider.kind,
            "upstream_prompt_managed_by_codex": provider.kind == "codex_chatgpt",
        }
        caps = provider.capabilities
        mode = (
            "json_schema"
            if caps.get("supports_json_schema")
            else ("json_object" if caps.get("supports_json_object", True) else "text")
        )
        modes = [mode] + (["json_object", "text"] if mode == "json_schema" else ["text"])
        modes = list(dict.fromkeys(modes))
        actual_messages = [
            *messages,
            {
                "role": "system",
                "content": "Return JSON matching this schema:\n" + json.dumps(schema, ensure_ascii=False),
            },
        ]
        params = generation_parameters(provider, temperature)
        estimate = estimate_tokens(json.dumps(actual_messages, ensure_ascii=False)) + estimate_tokens(
            json.dumps(schema)
        )
        if estimate + provider.max_output_tokens + 512 > provider.context_window:
            raise LLMError(
                "Budget de contexte dépassé : réduire les instructions ou augmenter la fenêtre du provider. "
                "La cible n’a pas été tronquée."
            )
        fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "messages": actual_messages,
                    "params": params,
                    "url": provider.base_url,
                    "transport": provider.kind,
                    "provider_id": provider.id,
                    "capabilities": caps,
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()
        with SessionLocal() as db:
            cached = db.scalar(
                select(RequestLog)
                .where(
                    RequestLog.project_id == project_id,
                    RequestLog.fingerprint == fingerprint,
                    RequestLog.status == "success",
                )
                .order_by(RequestLog.created_at.desc())
            )
            if cached:
                parsed = response_model.model_validate(cached.parsed)
                if validator:
                    validator(parsed)
                db.add(
                    RequestLog(
                        project_id=project_id,
                        segment_id=segment_id,
                        provider_id=provider_id,
                        operation=operation,
                        model=provider.model,
                        fingerprint=fingerprint,
                        parameters=params,
                        messages=actual_messages,
                        context=context or {},
                        parsed=parsed.model_dump(),
                        status="success",
                        cached=True,
                    )
                )
                db.commit()
                return parsed
        last_error = ""
        marker_repair_requested = False
        for attempt in range(1, 6):
            current_mode = modes[0]
            payload = wire_payload(
                provider.kind, params, actual_messages, schema, response_model.__name__, current_mode
            )
            request_id = await self.reserve(
                provider,
                project_id,
                segment_id,
                operation,
                fingerprint,
                actual_messages,
                payload,
                context or {},
                attempt,
            )
            start = time.monotonic()
            raw: dict = {}
            error: str | None = None
            transient = True
            unavailable = False
            authentication_required = False
            content_refused = False
            marker_error = False
            reasoning_failure = False
            truncated = False
            retry_after = 0
            try:
                async with (
                    asyncio.timeout(provider.timeout),
                    httpx.AsyncClient(
                        timeout=httpx.Timeout(provider.timeout, connect=min(10, provider.timeout)),
                        follow_redirects=False,
                        trust_env=False,
                    ) as client,
                ):
                    if provider.kind == "codex_chatgpt":
                        raw = await bridge_call(
                            provider.id,
                            "complete",
                            {**payload, "request_id": request_id},
                            timeout=provider.timeout + 5,
                        )
                        response = None
                    elif provider.kind == "anthropic":
                        from app.providers.anthropic import AnthropicProvider
                        # Direct provider handles its own request/response cycle
                        parsed = await AnthropicProvider().complete(
                            provider=provider,
                            messages=actual_messages,
                            response_model=response_model,
                            temperature=params.get("temperature"),
                        )
                        # Skip the rest of the loop, we already have parsed result
                        raw = {}
                        response = None
                    elif provider.kind == "openai_direct":
                        from app.providers.openai_direct import OpenAIDirectProvider
                        # Direct provider handles its own request/response cycle
                        parsed = await OpenAIDirectProvider().complete(
                            provider=provider,
                            messages=actual_messages,
                            response_model=response_model,
                            temperature=params.get("temperature"),
                        )
                        # Skip the rest of the loop, we already have parsed result
                        raw = {}
                        response = None
                    else:
                        endpoint = (
                            "/responses" if provider.kind == "openai_responses" else "/chat/completions"
                        )
                        response = await client.post(
                            provider.base_url + endpoint, headers=self.headers(provider), json=payload
                        )
                    if response is not None and response.status_code in (400, 422) and len(modes) > 1:
                        # Only capability-specific errors trigger a structured-output fallback.
                        body = response.text.lower()
                        if any(k in body for k in ("response_format", "json_schema", "json_object")):
                            modes.pop(0)
                            raise LLMError("Format structuré non supporté ; essai du format de repli.")
                    if response is not None:
                        response.raise_for_status()
                        raw = response.json()
                    if isinstance(raw, dict):
                        raw = normalize_response(provider.kind, raw)
                # Skip standard response parsing for direct providers
                if provider.kind in ("anthropic", "openai_direct"):
                    # parsed already set, validator already applied in provider
                    if validator:
                        validator(parsed)
                    error = None
                else:
                    if not isinstance(raw, dict):
                        raw = {"unexpected_shape": type(raw).__name__}
                        raise LLMError("Format de réponse du provider inattendu.")
                    if not raw.get("choices"):
                        raise LLMError("Réponse du provider sans choices exploitables.")
                    choice = raw["choices"][0]
                    message = choice.get("message", {})
                    refused = refusal_reason(raw, message.get("content") or "")
                    if refused:
                        raise ProviderContentRefused(refused)
                    if choice.get("finish_reason") not in ("stop", "eos_token"):
                        truncated = True
                        reasoning_failure = bool(
                            message.get("reasoning") or message.get("reasoning_content")
                        )
                        transient = False
                        raise LLMError(
                            "Réponse tronquée ou arrêt inattendu : " + str(choice.get("finish_reason"))
                        )
                    if not message.get("content") and (
                        message.get("reasoning") or message.get("reasoning_content")
                    ):
                        reasoning_failure = True
                        transient = False
                        raise LLMError(
                            "Le provider a renvoyé du raisonnement sans contenu final (content=null). "
                            "Désactivez ou réduisez le niveau de raisonnement."
                        )
                    if message.get("refusal"):
                        raise LLMError("Le provider a refusé la requête.")
                    parsed = parse_json(message.get("content") or "", response_model)
                    if validator:
                        validator(parsed)
            except (httpx.TimeoutException, TimeoutError):
                error = f"Timeout du provider après {provider.timeout} secondes."
                unavailable = True
            except httpx.ConnectError:
                error = "Connexion au provider impossible : vérifiez le DNS, l’URL et le service d’inférence."
                unavailable = True
            except httpx.HTTPStatusError as exc:
                error = (
                    f"Provider HTTP {exc.response.status_code}. Vérifier URL, authentification et capacités."
                )
                transient = exc.response.status_code in (408, 429, 500, 502, 503, 504)
                unavailable = transient
                authentication_required = exc.response.status_code in (401, 403)
                try:
                    response_body = exc.response.json()
                    content_refused = isinstance(response_body, dict) and refusal_http(
                        exc.response.status_code, response_body
                    )
                except ValueError:
                    content_refused = False
                try:
                    requested = float(exc.response.headers.get("Retry-After", "0"))
                    # "inf"/"nan" parse as floats but cannot become a delay.
                    retry_after = max(0, requested) if math.isfinite(requested) else 0
                except ValueError:
                    pass
                if provider.kind == "codex_chatgpt" and exc.response.status_code == 401:
                    error = (
                        "Codex déconnecté. Connectez le compte ChatGPT dans les paramètres de ce provider."
                    )
            except ProviderContentRefused as exc:
                error, content_refused, transient = str(exc), True, False
            except ValueError as exc:
                unavailable = False
                marker_error = "marqueurs de mise en forme" in str(exc).casefold()
                error = (
                    "La réponse a modifié les marqueurs EPUB immuables. "
                    "Libris conserve la traduction existante."
                    if marker_error
                    else f"Réponse invalide : {str(exc)[:1000] or type(exc).__name__}."
                )
            except (httpx.RequestError, ValidationError, LLMError, KeyError, TypeError) as exc:
                unavailable = isinstance(exc, httpx.RequestError)
                error = (
                    str(exc)[:1200]
                    if isinstance(exc, LLMError)
                    else f"Réponse invalide ({type(exc).__name__})."
                )
            except asyncio.CancelledError:
                try:
                    with SessionLocal() as db:
                        log = db.get(RequestLog, request_id)
                        if log and log.status == "running":
                            log.status, log.error = (
                                "interrupted",
                                "Requête interrompue par une pause ou un arrêt du worker.",
                            )
                            log.duration = time.monotonic() - start
                            db.commit()
                except SQLAlchemyError:
                    logger.warning("request=%s interruption_audit=deferred_database_unavailable", request_id)
                if provider.kind == "codex_chatgpt":
                    try:
                        await bridge_call(provider.id, "interrupt", {"request_id": request_id}, timeout=5)
                    except Exception:
                        pass  # Server-side disconnect monitoring and deadline remain the fallback.
                raise
            duration = time.monotonic() - start
            with SessionLocal() as db:
                log = db.get(RequestLog, request_id)
                if log is None:
                    raise LLMError("Le projet ou la requête ont été supprimés pendant le traitement.")
                log.raw = raw
                log.duration = duration
                log.status = "refused" if content_refused else "error" if error else "success"
                log.error = error or ""
                usage = raw.get("usage") or {}
                log.prompt_tokens = usage.get("prompt_tokens", 0)
                log.completion_tokens = usage.get("completion_tokens", 0)
                if not error:
                    log.parsed = parsed.model_dump()
                db.commit()
            logger.info(
                json.dumps(
                    {
                        "project": project_id,
                        "segment": segment_id,
                        "operation": operation,
                        "model": provider.model,
                        "duration": duration,
                        "status": log.status,
                        "attempt": attempt,
                        "error": error,
                    }
                )
            )
            if not error:
                return parsed
            last_error = error
            if content_refused:
                if operation in {
                    "translation",
                    "translation_review",
                    "translation_revision",
                    "polishing",
                } and attempt < 2:
                    continue
                raise ProviderContentRefused(error)
            if (
                reasoning_failure
                and provider.capabilities.get("supports_reasoning")
                and params.get("reasoning_effort") != "none"
            ):
                params["reasoning_effort"] = "none"
                continue
            if marker_error:
                if not marker_repair_requested:
                    marker_repair_requested = True
                    actual_messages = [
                        *actual_messages,
                        {
                            "role": "system",
                            "content": (
                                "Your previous response removed, added, or moved EPUB markers. "
                                "Retry once with the exact marker sequence from TARGET_TEXT, "
                                "including every opening and closing marker in the same order."
                            ),
                        },
                    ]
                    continue
                break
            if authentication_required:
                raise ProviderAuthenticationRequired(error)
            if unavailable:
                raise ProviderUnavailable(error, retry_after=retry_after)
            if not transient:
                break
            await asyncio.sleep(min(2 ** (attempt - 1), 16) + random.random())
        error_type = (
            InvalidResponseExhausted
            if attempt == 5 or marker_error or reasoning_failure or truncated
            else LLMError
        )
        raise error_type(
            f"{last_error} Échec après {attempt} tentative{'s' if attempt > 1 else ''}. "
            "Voir la requête pour le diagnostic."
        )

    async def reserve(
        self,
        provider: Provider,
        project_id: str,
        segment_id: str | None,
        operation: str,
        fingerprint: str,
        messages: list,
        parameters: dict,
        context: dict,
        attempt: int,
    ) -> str:
        # Row lock serializes admissions across API processes and workers; no lock held during inference.
        deadline = time.monotonic() + provider.timeout * 2
        while time.monotonic() < deadline:
            with SessionLocal() as db:
                db.scalar(select(Provider).where(Provider.id == provider.id).with_for_update())
                db.execute(
                    update(RequestLog)
                    .where(
                        RequestLog.provider_id == provider.id,
                        RequestLog.status == "running",
                        RequestLog.created_at <= time.time() - provider.timeout - 30,
                    )
                    .values(
                        status="abandoned", error="Requête interrompue ; aucun résultat validé enregistré."
                    )
                )
                active = db.scalar(
                    select(func.count())
                    .select_from(RequestLog)
                    .where(
                        RequestLog.provider_id == provider.id,
                        RequestLog.status == "running",
                        RequestLog.created_at > time.time() - provider.timeout - 30,
                    )
                )
                if active < provider.max_concurrency:
                    scope = execution.get()
                    log = RequestLog(
                        job_id=scope[0] if scope else None,
                        execution_owner=scope[1] if scope else "",
                        project_id=project_id,
                        segment_id=segment_id,
                        provider_id=provider.id,
                        operation=operation,
                        model=provider.model,
                        fingerprint=fingerprint,
                        messages=messages,
                        parameters=parameters,
                        context=context,
                        attempt=attempt,
                    )
                    db.add(log)
                    db.commit()
                    return log.id
            await asyncio.sleep(1)
        raise ProviderUnavailable("File du provider saturée ; nouvelle tentative planifiée.")


llm = OpenAIProvider()
