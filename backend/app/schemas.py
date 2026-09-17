from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Credentials(StrictModel):
    username: str = Field(min_length=2, max_length=80, pattern=r"^[\w.@-]+$")
    password: str = Field(min_length=12, max_length=200)


class Capabilities(StrictModel):
    supports_json_schema: bool = False
    supports_json_object: bool = True
    supports_reasoning: bool = False
    supports_tool_calls: bool = False
    reasoning_effort: Literal["", "none", "minimal", "low", "medium", "high", "xhigh"] = ""
    max_tokens_parameter: Literal["max_tokens", "max_completion_tokens"] = "max_tokens"


class ProviderInput(StrictModel):
    kind: Literal["openai", "openai_responses", "codex_chatgpt", "anthropic", "openai_direct"] = "openai"
    name: str = Field(min_length=1, max_length=100)
    base_url: str = Field(default="", max_length=500)
    api_key: str | None = Field(default=None, max_length=4000)
    model: str = Field(min_length=1, max_length=200)
    context_window: int = Field(default=32768, ge=2048, le=2_000_000)
    max_output_tokens: int = Field(default=4096, ge=256, le=200_000)
    temperature: float = Field(default=0.2, ge=0, le=2)
    top_p: float = Field(default=0.9, gt=0, le=1)
    timeout: int = Field(default=180, ge=5, le=3600)
    max_concurrency: int = Field(default=1, ge=1, le=16)
    capabilities: Capabilities = Field(default_factory=Capabilities)
    input_cost: float = Field(default=0, ge=0, le=10000)
    output_cost: float = Field(default=0, ge=0, le=10000)

    @field_validator("base_url")
    @classmethod
    def url(cls, value: str) -> str:
        if not value:
            return value
        parts = urlsplit(value)
        if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
            raise ValueError("URL HTTP(S) sans identifiants intégrés requise.")
        if parts.query or parts.fragment:
            raise ValueError("L’URL de base ne doit pas contenir de query ou fragment.")
        return value.rstrip("/")

    @model_validator(mode="after")
    def budget(self):
        if self.kind != "codex_chatgpt" and not self.base_url:
            raise ValueError("URL du provider requise.")
        if self.kind == "codex_chatgpt":
            self.base_url = ""
            if self.api_key:
                raise ValueError("La connexion ChatGPT utilise le code officiel, pas une clé API.")
        if self.max_output_tokens + 1024 >= self.context_window:
            raise ValueError("La fenêtre doit réserver au moins 1024 tokens aux entrées.")
        return self


class ProjectConfig(StrictModel):
    title: str = Field(min_length=1, max_length=500)
    author: str = Field(default="", max_length=500)
    series_name: str = Field(default="", max_length=500)
    volume_number: int | None = Field(default=None, ge=1, le=10000)
    source_language: str = Field(default="en", min_length=2, max_length=80)
    target_language: str = Field(default="fr", min_length=2, max_length=80)
    provider_id: str | None = None
    quality: Literal["fast", "normal", "high", "maximum"] = "normal"
    context_backend: Literal["internal", "openviking", "hybrid"] = "internal"
    instructions: str = Field(default="", max_length=20000)


class SeriesBatchInput(StrictModel):
    project_ids: list[str] = Field(min_length=1, max_length=100)
    series_name: str = Field(default="", max_length=500)
    first_volume: int = Field(default=1, ge=1, le=10000)
    mode: Literal["preserve", "sequential", "clear"] = "sequential"


class BatchExportInput(StrictModel):
    project_ids: list[str] = Field(min_length=1, max_length=100)


class GlossaryInput(StrictModel):
    source: str = Field(min_length=1, max_length=300)
    translation: str = Field(min_length=1, max_length=300)
    category: str = Field(default="autre", max_length=50)
    description: str = Field(default="", max_length=4000)
    locked: bool = False
    accepted: bool = True


class TextUnit(StrictModel):
    id: str
    text: str = Field(max_length=100_000)


class EditInput(StrictModel):
    revision: int = Field(ge=0)
    units: list[TextUnit] = Field(max_length=1000)
    validated: bool = False


class AcceptCritiqueInput(StrictModel):
    revision: int = Field(ge=0)


class Fact(StrictModel):
    text: str
    known_by: list[str] = Field(default_factory=list)
    kind: Literal["event", "belief", "relationship", "world_fact", "unresolved"] = "event"


class Character(StrictModel):
    canonical_name: str
    aliases: list[str] = Field(default_factory=list)
    proposed_aliases: list[str] = Field(default_factory=list)
    gender: str = ""
    pronouns: str = ""
    role: str = ""
    description: str = ""
    relationships: list[str] = Field(default_factory=list)
    speech_style: str = ""
    formal_or_informal: str = ""
    translation_notes: list[str] = Field(default_factory=list)


class Term(StrictModel):
    source: str
    translation: str
    category: str = "autre"
    description: str = ""


class RelationshipFact(StrictModel):
    source: str = Field(max_length=300)
    target: str = Field(max_length=300)
    relation_type: str = Field(max_length=80)
    description: str = Field(default="", max_length=2000)
    evidence: str = Field(default="", max_length=600)


class ChapterAnalysis(StrictModel):
    summary: str
    events: list[Fact] = Field(default_factory=list)
    characters: list[Character] = Field(default_factory=list)
    terms: list[Term] = Field(default_factory=list)
    style_notes: list[str] = Field(default_factory=list)
    relationships: list[RelationshipFact] = Field(default_factory=list, max_length=80)


class BookOverview(StrictModel):
    title: str = ""
    author: str = ""
    genre: list[str] = Field(default_factory=list)
    summary: str = ""
    tone: str = ""
    narrative_style: str = ""
    narrative_point_of_view: str = ""
    tense: str = ""
    target_audience: str = ""
    translation_guidelines: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)
    organizations: list[str] = Field(default_factory=list)
    important_objects: list[str] = Field(default_factory=list)
    world_specific_terms: list[str] = Field(default_factory=list)
    relationships: list[str] = Field(default_factory=list)
    known_wordplay: list[str] = Field(default_factory=list)
    honorifics: list[str] = Field(default_factory=list)
    formatting_conventions: list[str] = Field(default_factory=list)


class BookBible(BookOverview):
    characters: list[Character] = Field(default_factory=list)


class TranslationResult(StrictModel):
    units: list[TextUnit]
    new_terms: list[Term] = Field(default_factory=list)
    events: list[Fact] = Field(default_factory=list)
    uncertainties: list[str] = Field(default_factory=list)


class Critique(StrictModel):
    unit_id: str
    category: str
    severity: Literal["warning", "error"]
    description: str
    suggestion: str


class ReviewResult(StrictModel):
    issues: list[Critique] = Field(default_factory=list)


class FinalReviewResult(ReviewResult):
    decision: Literal["accept", "revise"]
    uncertainties: list[str] = Field(default_factory=list, max_length=0)
    explanation: str
    search_queries: list[str] = Field(default_factory=list, max_length=2)

    @model_validator(mode="after")
    def definitive_verdict(self):
        if self.decision == "accept" and self.issues:
            raise ValueError("Une décision d’acceptation ne peut pas conserver de problème.")
        if self.decision == "revise" and not self.issues:
            raise ValueError("Une décision de révision doit fournir une correction précise.")
        if self.decision == "revise" and any(not issue.suggestion.strip() for issue in self.issues):
            raise ValueError("Chaque problème doit fournir une correction directement applicable.")
        text = " ".join(
            [
                self.explanation,
                *(issue.description for issue in self.issues),
                *(issue.suggestion for issue in self.issues),
            ]
        ).casefold()
        deferred = (
            "laisser le choix à l’humain",
            "laisser le choix a l'humain",
            "à l’humain de décider",
            "a l'humain de decider",
            "au traducteur de décider",
            "impossible de trancher",
            "leave the choice to the human",
            "human should decide",
            "translator should decide",
            "cannot decide",
        )
        if any(phrase in text for phrase in deferred):
            raise ValueError("La revue finale doit statuer sans déléguer sa décision.")
        return self


class AskResult(StrictModel):
    answer: str
    variants: list[str] = Field(default_factory=list)
    references: list[str] = Field(default_factory=list)


class ContextNeeds(StrictModel):
    needs: list[str] = Field(max_length=4)


class JobInput(StrictModel):
    operation: Literal["analyze", "translate", "review", "consistency", "sync_memory", "resolve_validations"]
    chapter_id: str | None = None
    segment_id: str | None = None
    segment_ids: list[str] | None = Field(default=None, min_length=1, max_length=200)
    instruction: str = Field(default="", max_length=8000)
    deep: bool = False
    force: bool = False
    provider_id: str | None = None
    refused_only: bool = False
    continue_pipeline: bool = False


class AskInput(StrictModel):
    question: str = Field(min_length=1, max_length=4000)
    selection: str = Field(default="", max_length=8000)


class InstructionInput(StrictModel):
    instructions: str = Field(max_length=10000)
