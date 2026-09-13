"""Detect provider refusals separately from outages and invalid JSON."""

import json
import re

POLICY_CODES = {
    "content_filter",
    "content_policy_violation",
    "policy_violation",
    "safety_violation",
    "content_refusal",
    "moderation_blocked",
}


def refusal_text(text: str) -> bool:
    value = text.strip()[:1200]
    starts = re.match(
        r"(?is)^(?:(?:i(?:['’]m| am) sorry|sorry|je suis désolé(?:e)?|désolé(?:e)?)[,.:!\s]*(?:(?:but|mais)\s+)?)?(?:i (?:can(?:not|'t|’t)|won['’]t|am unable to)|je (?:ne |n['’])?(?:peux|suis pas en mesure))",
        value,
    )
    return bool(
        starts
        and re.search(
            r"(?i)translat|analy[sz]|tradu|analys|assist|help with|provide|contenu|content|policy|politiqu|sex|minor|interdit",
            value,
        )
    )


def refusal_reason(raw: dict, content: str) -> str | None:
    choice = (raw.get("choices") or [{}])[0]
    if choice.get("finish_reason") == "content_filter" or choice.get("message", {}).get("refusal"):
        return "Refus explicite du provider ou filtrage du contenu."
    if refusal_text(content):
        return "Le provider indique ne pas pouvoir traiter ce passage."
    try:
        parsed = json.loads(content)
    except (ValueError, TypeError):
        return None
    if not isinstance(parsed, dict):
        return None
    if parsed.get("refusal"):
        return "Refus déclaré dans la réponse structurée."
    if isinstance(parsed.get("summary"), str) and refusal_text(parsed["summary"]):
        return "Le résumé contient un refus au lieu d’une analyse."
    units = parsed.get("units")
    if (
        isinstance(units, list)
        and units
        and all(isinstance(u, dict) and refusal_text(str(u.get("text", ""))) for u in units)
    ):
        return "Les unités retournées contiennent un refus au lieu d’une traduction ; vérification humaine requise."
    return None


def refusal_http(status: int, body: dict) -> bool:
    error = body.get("error") or {}
    code = error.get("code") if isinstance(error, dict) else None
    return status == 451 or any(
        isinstance(value, str) and value in POLICY_CODES for value in (code, body.get("code"))
    )
