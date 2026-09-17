"""What a request log keeps. The prompt is stored once, in `messages`."""

from collections import Counter

EXCERPT = 200
DISCARDED_DETAILS = 12


def _item(item: dict, excerpt: bool) -> dict:
    content = item.get("content")
    if not isinstance(content, str):
        return item
    compact = {key: value for key, value in item.items() if key != "content"}
    compact["chars"] = len(content)
    if excerpt:
        compact["excerpt"] = content[:EXCERPT]
    return compact


def _query(trace: dict) -> dict:
    """Retrieval queries are built from the passage itself: keep their head, not a second copy."""
    compact = dict(trace)
    query = trace.get("query")
    if isinstance(query, str) and len(query) > EXCERPT:
        compact["query"], compact["query_chars"] = query[:EXCERPT], len(query)
    if isinstance(trace.get("openviking"), dict):
        compact["openviking"] = _query(trace["openviking"])
    return compact


def compact_trace(context: dict) -> dict:
    """Explain why each context item was kept or dropped without copying text the prompt already holds."""
    compact = dict(context)
    if isinstance(context.get("selected"), list):
        compact["selected"] = [_item(i, False) if isinstance(i, dict) else i for i in context["selected"]]
    dropped = context.get("discarded")
    summarized = "discarded_summary" in context  # Already compact: its totals must survive a second pass.
    if not summarized and isinstance(dropped, list) and all(isinstance(item, dict) for item in dropped):
        # A passage can discard more than a hundred candidates: count them all, detail the closest misses.
        ranked = sorted(dropped, key=lambda item: -float(item.get("relevance") or 0))
        compact["discarded"] = [_item(item, True) for item in ranked[:DISCARDED_DETAILS]]
        compact["discarded_summary"] = {
            "total": len(dropped),
            "by_reason": dict(Counter(str(item.get("reason", "")) for item in dropped)),
        }
    if isinstance(context.get("mandatory"), dict):
        compact["mandatory"] = sorted(context["mandatory"])
    if isinstance(context.get("retrieval"), dict):
        compact["retrieval"] = _query(context["retrieval"])
    return compact


def compact_parameters(payload: dict) -> dict:
    """Wire parameters without the conversation, which `messages` already records."""
    return {key: value for key, value in payload.items() if key not in {"messages", "input", "system"}}
