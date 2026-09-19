"""The translated EPUB of a volume, delivered without a person: residual passages keep their source,
EPUBCheck failures are repaired automatically.

A passage without a usable translation (none yet, source retained, markup codes that no longer match
its source) is written with its source text and listed. When EPUBCheck rejects the book, the files it
names fall back to their source text and the book is validated again, a bounded number of times
(DELIVERY_REPAIR_ATTEMPTS). Errors the original EPUB already had are inherited, not caused by the
translation: they are reported and tolerated, since no repair of the translation can remove them.
"""

import logging
from dataclasses import dataclass, field

from app.config import settings
from app.engines.epub import rebuild
from app.engines.epub.check import epubcheck
from app.engines.epub.text import validate_codes
from app.models import Segment

logger = logging.getLogger("epub.delivery")
BLOCKING = {"ERROR", "FATAL"}


class DeliveryFailed(Exception):
    """The artifact cannot be produced; `reason` is the French message given to the client."""

    def __init__(self, reason: str, details: list[str] | None = None):
        super().__init__(reason)
        self.reason = reason
        self.details = details or []


@dataclass
class EpubDelivery:
    content: bytes
    validation: dict
    # Passages written with their source: {"segment_id", "reason"}.
    fallbacks: list[dict] = field(default_factory=list)
    repairs: list[dict] = field(default_factory=list)
    inherited_errors: list[str] = field(default_factory=list)


def source_units(segment: Segment) -> list[dict]:
    return [{"id": unit["id"], "text": unit["text"]} for unit in segment.units]


def usable(segment: Segment) -> str | None:
    """Why this passage cannot be written translated, or None when it can."""
    if segment.retained_source:
        return "source_retained"
    if not segment.translation or not segment.translated_units:
        return "untranslated"
    translations = {unit["id"]: unit["text"] for unit in segment.translated_units}
    for unit in segment.units:
        if unit["id"] not in translations:
            return "untranslated"
        try:
            validate_codes(unit["text"], translations[unit["id"]])
        except ValueError:
            return "markup_mismatch"
    return None


def export_rows(segments: list[Segment], sourced: set[str]) -> list[dict]:
    rows = []
    for segment in segments:
        rows.append(
            {
                "id": segment.id,
                "units": segment.units,
                "translated_units": source_units(segment) if segment.id in sourced else segment.translated_units,
            }
        )
    return rows


def failures(validation: dict) -> list[dict]:
    if not validation.get("available") or validation.get("valid"):
        return []
    return [m for m in validation.get("report", {}).get("messages", []) if m.get("severity") in BLOCKING]


def paths(message: dict) -> list[str]:
    return [place["path"] for place in message.get("locations", []) if place.get("path")]


def keys(messages: list[dict]) -> set[tuple[str, str]]:
    return {(m.get("ID", ""), path) for m in messages for path in (paths(m) or [""])}


def describe(message: dict) -> str:
    where = paths(message)
    return f"{message.get('ID', '')} — {str(message.get('message', ''))[:300]}" + (f" ({where[0]})" if where else "")


def check(content: bytes) -> dict:
    try:
        return epubcheck(content)
    except ValueError as exc:
        # The validator itself is unavailable for now (queue full, timeout): not a defect of the book.
        return {"available": False, "valid": None, "message": str(exc)}


def resources_of(segment: Segment) -> set[str]:
    return {unit.get("resource", "") for unit in segment.units}


def deliver_epub(original: bytes, segments: list[Segment], language: str, title: str, author: str) -> EpubDelivery:
    sourced: dict[str, str] = {}
    for segment in segments:
        reason = usable(segment)
        if reason:
            sourced[segment.id] = reason
    repairs: list[dict] = []
    inherited: set[tuple[str, str]] | None = None
    attempts = settings().delivery_repair_attempts
    for attempt in range(attempts + 1):
        try:
            content = rebuild(original, export_rows(segments, set(sourced)), language, title, author)
        except ValueError as exc:
            # The source itself cannot be rebuilt (an anchor lost): nothing to fall back to.
            raise DeliveryFailed("Impossible de reconstruire l’EPUB traduit.", [str(exc)[:300]]) from None
        validation = check(content)
        found = failures(validation)
        if inherited is None and found:
            inherited = keys(failures(check(original)))
        new = [m for m in found if not keys([m]) <= (inherited or set())]
        if not new:
            return EpubDelivery(
                content,
                validation,
                [{"segment_id": sid, "reason": reason} for sid, reason in sourced.items()],
                repairs,
                sorted({describe(m) for m in found}),
            )
        if attempt == attempts:
            break
        named = {path for m in new for path in paths(m)}
        targets = [
            s for s in segments if s.id not in sourced and (resources_of(s) & named or not named)
        ]
        if not targets:
            break
        for segment in targets:
            sourced[segment.id] = "epubcheck_repair"
        repairs.append(
            {
                "attempt": attempt + 1,
                "errors": sorted({describe(m) for m in new})[:20],
                "files": sorted(named),
                "passages_restored_to_source": len(targets),
            }
        )
        logger.info(
            "delivery=epub_repair attempt=%s files=%s passages=%s", attempt + 1, len(named), len(targets)
        )
    raise DeliveryFailed(
        "EPUBCheck refuse l’EPUB traduit, même après réparation automatique.",
        sorted({describe(m) for m in new})[:20],
    )
