"""JSON translation requests of the automation API, turned into the same chapters as TXT files.

The payload is validated strictly (unknown fields refused, nothing fetched from a URL it names) and
each chapter goes through `text_chapter`, so a JSON chapter and a TXT file with the same text give the
same passages, layout and checksum. Resources are derived from the volume and the chapter's external
identifier (or number): sending the same chapter again finds the same units.
"""

import json
import re
from typing import Annotated, Literal

from pydantic import AfterValidator, Field, ValidationError, ValidationInfo, field_validator, model_validator

from app.engines.ingestion.base import ImportedAsset, ImportedChapter
from app.engines.ingestion.passages import passage_chars
from app.engines.ingestion.store import text_resource
from app.engines.ingestion.text import text_chapter
from app.models import Project
from app.schemas import StrictModel

SCHEMA_VERSION = 1
# language[-Script][-REGION][-variant…]: en, fr-FR, zh-Hant, zh-Hant-TW, es-419, de-CH-1996.
BCP47 = re.compile(
    r"^[A-Za-z]{2,3}(-[A-Za-z]{4})?(-(?:[A-Za-z]{2}|[0-9]{3}))?(-(?:[A-Za-z0-9]{5,8}|[0-9][A-Za-z0-9]{3}))*$"
)
IDENTIFIER = r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}$"


def language(value: str) -> str:
    if not BCP47.match(value):
        raise ValueError("Code de langue invalide : utilisez une étiquette BCP 47 (en, fr-FR, zh-Hant…).")
    return value


Language = Annotated[str, Field(max_length=35), AfterValidator(language)]


class SeriesReference(StrictModel):
    id: str | None = Field(default=None, max_length=36)
    name: str | None = Field(default=None, max_length=500)
    create_if_missing: bool = True

    @model_validator(mode="after")
    def named(self):
        if not self.id and not (self.name or "").strip():
            raise ValueError("Indiquez la série : son identifiant ou son nom.")
        return self


class VolumeReference(StrictModel):
    external_id: str | None = Field(default=None, pattern=IDENTIFIER)
    # Follow-up of a webnovel: the chapters go to the series' last volume (volume 1 when it has none).
    # Declared before `number`, which is checked against it.
    latest: bool = False
    number: int | None = Field(default=None, ge=1, le=10000, validate_default=True)
    title: str = Field(default="", max_length=500)

    @field_validator("number")
    @classmethod
    def numbered(cls, value: int | None, info: ValidationInfo) -> int | None:
        latest = info.data.get("latest", False)
        if value is None and not latest:
            raise ValueError("Indiquez le numéro du volume, ou « latest: true » pour le dernier volume de la série.")
        if value is not None and latest:
            raise ValueError("Indiquez le numéro du volume ou « latest: true », pas les deux.")
        return value


class ChapterInput(StrictModel):
    external_id: str | None = Field(default=None, pattern=IDENTIFIER)
    number: float = Field(ge=0, le=100000, allow_inf_nan=False)
    title: str = Field(default="", max_length=500)
    content: str

    @field_validator("content")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Le contenu du chapitre est vide.")
        return value


class PipelineOptions(StrictModel):
    start: bool = True
    provider_id: str | None = Field(default=None, max_length=36)
    quality: Literal["fast", "normal", "high", "maximum"] | None = None
    context_backend: Literal["internal", "openviking", "hybrid"] | None = None
    final_review: bool = True
    # Place in the fair queue, within what the token allows (app.jobs.fairness); None: normal.
    priority: Literal["low", "normal", "high"] | None = None
    # Analysis mode and passages worked on at once for this request (None: the volume's choice, else
    # ANALYSIS_MODE and the provider's capacity shared between books).
    analysis_mode: Literal["parallel", "strict"] | None = None
    threads: int | None = Field(default=None, ge=1, le=64)


class OutputOptions(StrictModel):
    format: Literal["json", "txt", "txt-zip", "epub-bilingual"] = "json"


# Webhook events a request can ask for on top of `translation_request.finished`, which is always sent.
CallbackEvent = Literal["chapters.translated"]


class TranslationPayload(StrictModel):
    external_id: str | None = Field(default=None, pattern=IDENTIFIER)
    series: SeriesReference
    volume: VolumeReference
    author: str = Field(default="", max_length=500)
    source_language: Language
    target_language: Language
    chapters: list[ChapterInput] = Field(min_length=1)
    # A chapter already imported with another text is refused unless this is true.
    replace_changed_chapters: bool = False
    # A replaced chapter keeps no human edit: true lets an automation overwrite them without a person.
    discard_human: bool = False
    pipeline: PipelineOptions = Field(default_factory=PipelineOptions)
    output: OutputOptions = Field(default_factory=OutputOptions)
    # Webhook called by the worker when the request ends (see app.engines.delivery.webhooks).
    callback_url: str | None = Field(default=None, max_length=2000)
    callback_events: list[CallbackEvent] = Field(default_factory=list, max_length=5)

    @model_validator(mode="after")
    def distinct_chapters(self):
        numbers = [chapter.number for chapter in self.chapters]
        for number in sorted({n for n in numbers if numbers.count(n) > 1}):
            raise ValueError(f"Le chapitre {number:g} apparaît plusieurs fois dans la requête.")
        identifiers = [chapter.external_id for chapter in self.chapters if chapter.external_id]
        for identifier in sorted({i for i in identifiers if identifiers.count(i) > 1}):
            raise ValueError(f"L’identifiant de chapitre « {identifier} » apparaît plusieurs fois dans la requête.")
        return self

    def canonical(self) -> bytes:
        """The stored form: two requests with the same meaning have the same bytes and checksum.
        Fields added in 0.6 and later are left out while unset, so an older request keeps its checksum."""
        data = self.model_dump(mode="json")
        for key, default in (("discard_human", False), ("callback_url", None)):
            if data.get(key) == default:
                data.pop(key, None)
        if not data.get("callback_events"):
            data.pop("callback_events", None)
        else:
            data["callback_events"] = sorted(set(data["callback_events"]))
        if not data["volume"].get("latest"):
            data["volume"].pop("latest", None)
        # The priority changes when the work runs, not the work: the same request either way.
        data.get("pipeline", {}).pop("priority", None)
        return json.dumps(data, ensure_ascii=False, sort_keys=True).encode()


class PayloadRejected(ValueError):
    """Invalid payload; `errors` lists every problem without echoing the submitted text."""

    def __init__(self, errors: list[dict]):
        super().__init__("Requête de traduction invalide.")
        self.errors = errors


def parse_payload(data: bytes | str | dict, max_chapters: int) -> TranslationPayload:
    try:
        raw = json.loads(data) if isinstance(data, (bytes, str)) else data
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PayloadRejected(
            [{"loc": [], "msg": "Le corps de la requête n’est pas un JSON UTF-8 valide.", "type": type(exc).__name__}]
        ) from None
    if not isinstance(raw, dict):
        raise PayloadRejected([{"loc": [], "msg": "Le corps de la requête doit être un objet JSON.", "type": "type"}])
    chapters = raw.get("chapters")
    if isinstance(chapters, list) and len(chapters) > max_chapters:
        # Refused before validating thousands of chapters one by one.
        raise PayloadRejected(
            [{"loc": ["chapters"], "msg": f"Trop de chapitres : {max_chapters} au maximum par requête.", "type": "too_long"}]
        )
    try:
        return TranslationPayload.model_validate(raw)
    except ValidationError as exc:
        # Never the submitted value ("input"): it can be a whole chapter.
        raise PayloadRejected(
            [
                {"loc": list(item["loc"]), "msg": str(item["msg"]).removeprefix("Value error, "), "type": item["type"]}
                for item in exc.errors()
            ]
        ) from None


def payload_asset(payload: TranslationPayload) -> ImportedAsset:
    data = payload.canonical()
    return ImportedAsset(
        name=f"{payload.external_id or 'request'}.json",
        format="json",
        media_type="application/json",
        data=data,
        meta={"schema_version": SCHEMA_VERSION},
    )


def chapter_key(chapter: ChapterInput) -> str:
    return f"id:{chapter.external_id}" if chapter.external_id else f"{chapter.number:g}"


def payload_chapters(project: Project, payload: TranslationPayload, max_length: int) -> list[ImportedChapter]:
    """The request's chapters in number order, with the resources of `text_resource`."""
    chapters = []
    for index, item in sorted(enumerate(payload.chapters), key=lambda pair: pair[1].number):
        # Without a title nothing is added to the text: the chapter is only named by its number.
        title = item.title.strip()
        try:
            chapter, warnings = text_chapter(
                item.content, title=title, resource=text_resource(project, "json", chapter_key(item)),
                max_chars=passage_chars(project), max_length=max_length,
            )
        except ValueError as exc:
            raise PayloadRejected([{"loc": ["chapters", index, "content"], "msg": str(exc), "type": "value_error"}]) from None
        chapter.title = chapter.title or f"{item.number:g}"
        chapter.number = item.number
        chapter.external_id = item.external_id
        chapter.meta.update({"adapter": "json", "version": 1, "warnings": warnings})
        chapters.append(chapter)
    return chapters
