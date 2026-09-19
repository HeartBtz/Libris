"""Files sent to the automation API: an EPUB, or TXT chapters turned into the JSON request they mean.

TXT chapters become a `TranslationPayload`, so they follow exactly the path of JSON chapters
(idempotence, volume lookup, conflicts, results). Chapter numbers are read from the file names; when a
name gives none, or the same one twice, the file's place in the upload decides, and the decision is
recorded with its reason (never silent, never a question to a person).
"""

import hashlib
import json
from typing import Annotated, Literal, get_args

from pydantic import Field, ValidationError, model_validator

from app.engines.ingestion.naming import LOW, clean_title, propose_chapters
from app.engines.ingestion.payload import (
    IDENTIFIER,
    CallbackEvent,
    Language,
    PayloadRejected,
    TranslationPayload,
    parse_payload,
)
from app.engines.ingestion.text import TextRejected, decode
from app.schemas import StrictModel

Quality = Literal["fast", "normal", "high", "maximum"]


class UploadOptions(StrictModel):
    """Options of a file upload: form fields (multipart) or query parameters (raw EPUB body)."""

    external_id: str | None = Field(default=None, pattern=IDENTIFIER)
    series: str | None = Field(default=None, max_length=500)
    series_id: str | None = Field(default=None, max_length=36)
    # A number, or "latest": the series' last volume (TXT chapters following up a webnovel).
    volume: Annotated[int, Field(ge=1, le=10000)] | Literal["latest"] | None = None
    volume_external_id: str | None = Field(default=None, pattern=IDENTIFIER)
    title: str = Field(default="", max_length=500)
    author: str = Field(default="", max_length=500)
    source_language: Language | None = None
    target_language: Language | None = None
    provider_id: str | None = Field(default=None, max_length=36)
    quality: Quality | None = None
    context_backend: Literal["internal", "openviking", "hybrid"] | None = None
    start: bool = True
    final_review: bool = True
    output_format: Literal["json", "txt", "txt-zip", "epub", "epub-bilingual"] | None = None
    callback_url: str | None = Field(default=None, max_length=2000)
    # Comma-separated extra webhook events, e.g. "chapters.translated".
    callback_events: str = Field(default="", max_length=200)
    replace_changed_chapters: bool = False
    discard_human: bool = False
    # Place in the fair queue, within what the token allows (app.jobs.fairness); empty: normal.
    priority: Literal["low", "normal", "high"] | None = None

    @model_validator(mode="after")
    def one_series(self):
        if self.series and self.series_id:
            raise ValueError("Indiquez la série par son nom ou par son identifiant, pas les deux.")
        unknown = [item for item in callback_events(self) if item not in get_args(CallbackEvent)]
        if unknown:
            raise ValueError(f"Événement de webhook inconnu : {', '.join(unknown)}.")
        return self


def upload_options(values: dict[str, str]) -> UploadOptions:
    """Form and query values are strings; empty ones mean "not given"."""
    cleaned = {key: value for key, value in values.items() if value != ""}
    try:
        return UploadOptions.model_validate(cleaned)
    except ValidationError as exc:
        raise PayloadRejected(
            [
                {
                    "loc": list(item["loc"]),
                    "msg": str(item["msg"]).removeprefix("Value error, "),
                    "type": item["type"],
                }
                for item in exc.errors()
            ]
        ) from None


def callback_events(options: UploadOptions) -> list[str]:
    return [item.strip() for item in options.callback_events.split(",") if item.strip()]


def epub_digest(data: bytes, options: UploadOptions) -> str:
    """Same file and same options: same request (idempotence); the callback does not change the work."""
    meaning = options.model_dump(mode="json", exclude={"callback_url", "callback_events", "priority"})
    return hashlib.sha256(
        hashlib.sha256(data).digest() + json.dumps(meaning, sort_keys=True).encode()
    ).hexdigest()


def chapter_numbers(names: list[str]) -> tuple[list[float], list[dict]]:
    """A number per file and the decisions taken for the ones the names did not settle."""
    guesses = propose_chapters(names)
    numbers: list[float] = []
    decisions = []
    taken = {g.value for g in guesses if g.value is not None}
    counts = [g.value for g in guesses]
    fallback = 1.0
    for index, (name, guess) in enumerate(zip(names, guesses, strict=True)):
        if guess.value is not None and counts.count(guess.value) == 1:
            numbers.append(guess.value)
            if guess.confidence == LOW:
                decisions.append({"file": index, "name": name, "chapter_number": guess.value, "confidence": LOW,
                                  "reason": f"numéro peu sûr accepté : {guess.reason}"})  # fmt: skip
            continue
        while fallback in taken:
            fallback += 1
        why = "même numéro que d’autres fichiers" if guess.value is not None else "aucun numéro dans le nom"
        decisions.append(
            {"file": index, "name": name, "chapter_number": fallback, "confidence": LOW,
             "reason": f"{why} : numéro donné par l’ordre d’envoi"}
        )  # fmt: skip
        numbers.append(fallback)
        taken.add(fallback)
    return numbers, decisions


def text_payload(
    files: list[tuple[str, bytes]], options: UploadOptions, max_chapters: int
) -> tuple[TranslationPayload, list[dict]]:
    """TXT chapters as the JSON request they stand for, and the decisions it took."""
    if len(files) > max_chapters:
        raise PayloadRejected(
            [
                {
                    "loc": ["files"],
                    "msg": f"Trop de chapitres : {max_chapters} au maximum par requête.",
                    "type": "too_long",
                }
            ]
        )
    if options.volume is None:
        raise PayloadRejected([{"loc": ["volume"], "msg": "Indiquez le numéro du volume (champ « volume »), "
                                "ou « latest » pour le dernier volume de la série.", "type": "missing"}])  # fmt: skip
    if not options.series and not options.series_id:
        raise PayloadRejected([{"loc": ["series"], "msg": "Indiquez la série : son identifiant ou son nom.",
                                "type": "missing"}])  # fmt: skip
    if not options.source_language or not options.target_language:
        raise PayloadRejected([{"loc": ["source_language"], "msg": "Indiquez la langue source et la langue cible.",
                                "type": "missing"}])  # fmt: skip
    names = [name for name, _ in files]
    numbers, decisions = chapter_numbers(names)
    chapters, errors = [], []
    for index, ((name, data), number) in enumerate(zip(files, numbers, strict=True)):
        try:
            text, encoding, _ = decode(data)
        except TextRejected as exc:
            errors.append({"loc": ["files", index], "msg": f"« {name} » : {exc}", "type": "value_error"})
            continue
        if encoding == "windows-1252":
            decisions.append({"file": index, "name": name, "encoding": encoding,
                              "reason": "fichier non UTF-8 lu en Windows-1252"})  # fmt: skip
        chapters.append({"number": number, "title": clean_title(name), "content": text})
    if errors:
        raise PayloadRejected(errors)
    raw = {
        "external_id": options.external_id,
        "series": {"id": options.series_id, "name": options.series, "create_if_missing": True},
        "volume": {"external_id": options.volume_external_id, "title": options.title,
                   **({"latest": True} if options.volume == "latest" else {"number": options.volume})},
        "author": options.author,
        "source_language": options.source_language,
        "target_language": options.target_language,
        "chapters": chapters,
        "replace_changed_chapters": options.replace_changed_chapters,
        "discard_human": options.discard_human,
        "pipeline": {
            "start": options.start, "provider_id": options.provider_id, "quality": options.quality,
            "context_backend": options.context_backend, "final_review": options.final_review,
        },
        "output": {"format": options.output_format or "json"},
        "callback_url": options.callback_url,
        "callback_events": callback_events(options),
    }  # fmt: skip
    if options.output_format == "epub":
        raise PayloadRejected([{"loc": ["output_format"], "msg": "Le format EPUB n’est disponible que pour un EPUB envoyé.",
                                "type": "value_error"}])  # fmt: skip
    return parse_payload(raw, max_chapters), decisions
