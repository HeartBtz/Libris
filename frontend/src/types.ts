export interface User {
  id: string;
  username: string;
  admin: boolean;
  active?: boolean;
}
export interface Stats {
  reviewed_segments?: number;
  review_total?: number;
  retained_source: number;
  analyzed_segments: number;
  synthesized_chapters: number;
  total: number;
  translated: number;
  validated: number;
  flagged: number;
  errors: number;
  refused: number;
  chapters: number;
  glossary: number;
  translation_memory_reused?: number;
}
export interface Project {
  id: string;
  owner_id: string;
  title: string;
  author: string;
  series_name: string;
  volume_number: number | null;
  archived_at: number | null;
  source_language: string;
  target_language: string;
  provider_id: string | null;
  quality: string;
  context_backend: string;
  instructions: string;
  translation_memory?: boolean;
  status: string;
  stats: Stats;
  updated_at: number;
  book_info: {
    words: number;
    images: number;
    size: number;
    validation?: unknown;
    untranslated?: Record<string, { count: number; resources: string[] }>;
  };
  /** Only the project detail carries the Book Bible; the list omits it. */
  bible?: Record<string, unknown>;
  progress?: ProjectProgress;
  /** Absent on servers before 0.6; no series means a standalone volume. */
  series_id?: string | null;
  /** Where the volume's text came from: an EPUB, TXT chapter files or a JSON payload. */
  source_format?: SourceFormat;
  /** `serial` is the continuous chapter container of a webnovel. */
  project_kind?: "volume" | "serial";
  external_id?: string | null;
  import_meta?: Record<string, unknown>;
  /** Settings kept with the book: autopilot, fallback providers, review mode, passage size… */
  config?: ProjectSettingsConfig;
}
export interface ProjectSettingsConfig {
  /** Absent: the installation's AUTOPILOT_ENABLED decides. */
  autopilot?: boolean | null;
  /** Tried in this order when the book's provider is down, after the job's own provider. */
  fallback_provider_ids?: string[] | null;
  /** Absent: REVIEW_MODE. */
  review_mode?: "separate" | "fused" | null;
  /** Absent: PASSAGE_MAX_CHARS; only chapters imported later are cut with it. */
  passage_max_chars?: number | null;
  translation_memory?: boolean;
}
export type SourceFormat = "epub" | "txt" | "json" | "md" | "html" | "docx";
/** Formats the import assistant sends to `/api/imports`; every one but EPUB is one chapter per file. */
export type ImportFormat = "epub" | "txt" | "md" | "html" | "docx";
export type Quality = "fast" | "normal" | "high" | "maximum";
export type ContextBackend = "internal" | "openviking" | "hybrid";
export interface Series {
  id: string;
  owner_id: string;
  name: string;
  kind: "books" | "webnovel";
  authors: string[];
  source_language: string | null;
  target_language: string | null;
  provider_id: string | null;
  quality: Quality | null;
  context_backend: ContextBackend | null;
  instructions: string;
  bible_validated: boolean;
  archived_at: number | null;
  created_at: number;
  updated_at: number;
  /** Seen through a shared volume: read-only. */
  shared: boolean;
  volumes: number;
  serial: boolean;
  chapters: number;
  formats: SourceFormat[];
  progress: { total: number; translated: number; validated: number; percent: number; running: number };
  issues: { flagged: number; errors: number; context_stale: number };
  activity: number;
  providers: { id: string; name: string; model: string }[];
  memory: { backends: string[]; pending: number; failed: number };
  missing_volumes: number[];
  duplicate_volumes: number[];
}
export interface SeriesDetail extends Series {
  bible: Record<string, unknown>;
  /** Volumes in reading order. */
  volume_list: Project[];
}
export interface SeriesChapter {
  id: string;
  project_id: string;
  volume_title: string;
  position: number;
  title: string;
  chapter_number: number | null;
  external_id: string | null;
  analyzed: boolean;
  context_stale: boolean;
  segments: number;
  translated: number;
  validated: number;
  flagged: number;
}
export interface SeriesTerm {
  id: string;
  series_id: string;
  source: string;
  translation: string;
  category: string;
  description: string;
  locked: boolean;
  accepted: boolean;
  origin: "human" | "volume";
  first_project_id: string | null;
  first_volume_number: number | null;
}
export interface GlossaryOverride {
  project_id: string;
  volume_title: string;
  volume_number: number | null;
  source: string;
  translation: string;
  locked: boolean;
}
export type EntityCategory = "character" | "location" | "organization" | "object";
export interface SeriesEntityLink {
  id: string;
  series_entity_id: string;
  entity_id: string;
  project_id: string;
  status: "linked" | "proposed" | "rejected";
  confidence: number;
  reason: string;
  human: boolean;
  local_name: string;
  local_aliases: string[];
  volume_title: string;
  volume_number: number | null;
}
export interface SeriesEntity {
  id: string;
  series_id: string;
  name: string;
  category: EntityCategory;
  aliases: string[];
  data: Record<string, unknown>;
  validated: boolean;
  first_project_id: string | null;
  first_volume_number: number | null;
  merged_into_id: string | null;
  links: SeriesEntityLink[];
}
export interface SeriesRelation {
  id: string;
  source_id: string;
  target_id: string;
  source: string;
  target: string;
  relation_type: string;
  description: string;
  evidence: string;
  first_volume_number: number | null;
  validated: boolean;
}
export interface AuditEntry {
  id: string;
  actor_id: string | null;
  project_id: string | null;
  action: string;
  detail: Record<string, unknown>;
  created_at: number;
}
export interface SeriesMemory {
  backends: string[];
  configured: boolean;
  root_uri: string;
  pending: number;
  failed: number;
  sent: number;
  volumes: {
    project_id: string;
    title: string;
    volume_number: number | null;
    context_backend?: ContextBackend;
    pending: number;
    failed: number;
    sent: number;
  }[];
}
export type Confidence = "high" | "medium" | "low";
/** One uploaded file as the server inspected it; nothing is imported yet. */
export interface FileInspection {
  index: number;
  name: string;
  size: number;
  sha256: string;
  duplicate:
    | null
    | { kind: "batch"; index: number; name: string }
    | { kind: "library"; project_id: string; title: string };
  format: ImportFormat;
  title: string;
  author: string;
  language: string;
  series: string;
  series_index: number | null;
  chapter_number: number | null;
  number_confidence: Confidence;
  number_reason: string;
  warnings: string[];
  errors: string[];
  meta: Record<string, unknown>;
}
export interface VolumeProposal {
  index: number;
  title: string;
  volume_number: number | null;
  confidence: Confidence;
  reason: string;
  warnings: string[];
  existing_volume: null | { project_id: string; title: string };
}
export interface ChapterProposal {
  index: number;
  title: string;
  chapter_number: number | null;
  confidence: Confidence;
  reason: string;
  existing_chapter: null | { chapter_id: string; title: string; same_content: boolean };
}
export interface Proposal {
  series?: { name: string; confidence: Confidence; series_reason: string; existing_series_id: string | null };
  items: (VolumeProposal | ChapterProposal)[];
  duplicate_numbers: number[];
  missing_numbers: number[];
}
export interface ImportResult {
  series_id: string | null;
  series_name: string;
  projects: { id: string; title: string; volume_number: number | null; status: "created" | "updated" }[];
  chapters: {
    created: number;
    unchanged: number;
    replaced: number;
    items: { index: number; chapter_id: string; status: string }[];
  };
  jobs: { project_id: string; job_id: string }[];
  warnings: string[];
  views?: Project[];
  /** Numbers the server decided instead of asking a person, each with its reason. */
  decisions?: ImportDecision[];
}
export interface ImportDecision {
  index: number;
  name: string;
  volume_number?: number | null;
  chapter_number?: number | null;
  reason: string;
}
export interface ImportSession {
  id: string;
  format: ImportFormat;
  files: FileInspection[];
  expires_at: number;
  result: ImportResult | null;
  proposal?: Proposal;
  /** True when the server refuses an unconfirmed low-confidence number (IMPORT_CONFIRM_LOW_CONFIDENCE). */
  confirm_low_confidence?: boolean;
}
export interface ProgressStage {
  key: "import" | "analysis" | "translation" | "review" | "export";
  label: string;
  done: number;
  total: number;
  percent: number;
}
export interface ProjectProgress {
  active_stage: ProgressStage["key"];
  state: string;
  operation: string | null;
  job_id: string | null;
  next_attempt?: number;
  stop_reason?: string;
  model: string | null;
  current: ProgressStage;
  stages: ProgressStage[];
  review: {
    examined: number;
    total: number;
    resolved: number;
    needs_human: number;
    remaining: number;
    protected: number;
    revised: number;
    failed: number;
  };
  estimate: {
    remaining_seconds: number | null;
    remaining_cost: number | null;
    spent_cost: number;
    confidence: "insufficient" | "low" | "medium" | "high" | "complete";
  };
}
/** What every user may see of a provider; the connection details are for administrators only. */
export interface ProviderSummary {
  id: string;
  kind: "openai" | "openai_responses" | "codex_chatgpt" | "anthropic" | "openai_direct";
  name: string;
  model: string;
}
export interface Provider extends ProviderSummary {
  created_at?: number;
  base_url: string;
  context_window: number;
  max_output_tokens: number;
  temperature: number;
  top_p: number;
  timeout: number;
  max_concurrency: number;
  input_cost: number;
  output_cost: number;
  has_api_key?: boolean;
  capabilities: {
    supports_json_schema?: boolean;
    supports_json_object?: boolean;
    supports_reasoning?: boolean;
    supports_tool_calls?: boolean;
    reasoning_effort?: string;
    max_tokens_parameter?: string;
  };
}
export interface Chapter {
  id: string;
  title: string;
  position: number;
  resource: string;
  instructions: string;
  analyzed: boolean;
  /** Navigation and metadata sections are translated but not counted in the book's chapters. */
  kind?: "narrative" | "auxiliary" | "navigation" | "metadata";
}
export interface Unit {
  id: string;
  text: string;
}
export interface Critique {
  unit_id: string;
  category: string;
  severity: "warning" | "error";
  description: string;
  suggestion: string;
  queued?: boolean;
}
export interface Segment {
  retained_source: boolean;
  id: string;
  project_id: string;
  chapter_id: string;
  position: number;
  section: string;
  source: string;
  units: Unit[];
  translated_units: Unit[];
  translation: string;
  status: string;
  stage: string;
  human: boolean;
  validated: boolean;
  revision: number;
  instructions: string;
  error: string;
  uncertainties: string[];
  critique: Critique[];
}
export interface Job {
  /** Absent on servers before 0.6. */
  provider_id?: string | null;
  options?: Record<string, unknown>;
  result?: { autopilot?: AutopilotResult } & Record<string, unknown>;
  created_at?: number;
  finished_at?: number | null;
  next_attempt: number;
  outage_count: number;
  stop_reason: string;
  id: string;
  status: string;
  operation: string;
  error: string;
  checkpoint: Record<string, unknown>;
  attempts: number;
}
export interface Term {
  id: string;
  source: string;
  translation: string;
  category: string;
  description: string;
  locked: boolean;
  accepted: boolean;
  /** This volume deliberately departs from its series glossary for this term. */
  series_override?: boolean;
}
export interface Version {
  id: string;
  origin: string;
  units: Unit[];
  created_at: number;
  applied: boolean;
}
export interface LLMRequest {
  id: string;
  operation: string;
  model: string;
  status: string;
  duration: number;
  prompt_tokens: number;
  completion_tokens: number;
  error: string;
  attempt: number;
  cached: boolean;
  created_at: number;
  messages?: { role: string; content: string }[];
  context?: Record<string, unknown>;
  raw?: unknown;
  parsed?: unknown;
}
export interface Issue {
  id: string;
  project_id: string;
  segment_id: string | null;
  severity: string;
  code: string;
  message: string;
  resolved: boolean;
  created_at: number;
}
type Task = () => Promise<void>;
/** `run` reports a user action; `run.background` is for automatic loads and never hides a shown error. */
export type Run = ((task: Task) => Promise<void>) & {
  background: (task: Task) => Promise<void>;
};
export type AutopilotOutcome = "completed" | "completed_with_residuals" | "failed";
export interface AutopilotResidual {
  segment_id: string;
  chapter_id: string;
  reason: string;
}
/** `job.result.autopilot`: how a whole-book autopilot run ended. */
export interface AutopilotResult {
  outcome: AutopilotOutcome;
  rounds: number;
  residuals: AutopilotResidual[];
  reason: string | null;
}
export interface AutopilotDecision {
  id: string;
  project_id: string;
  job_id: string | null;
  segment_id: string | null;
  stage: string;
  kind: string;
  action: string;
  reason: string;
  provider: string;
  model: string;
  created_at: number;
}
/** `GET /api/projects/{id}/autopilot`. */
export interface AutopilotView {
  enabled: boolean;
  settings: {
    max_rounds: number;
    fallback_provider_ids: string[];
    outage_max_retries: number;
    outage_max_wait_seconds: number;
  };
  report: (AutopilotResult & { job_id: string; status: string; finished_at: number | null }) | null;
  decisions: { items: AutopilotDecision[]; total: number; limit: number; offset: number };
}
/** Glossary files and shared glossaries. */
export type GlossaryField = "source" | "translation" | "category" | "description" | "locked" | "accepted";
export type GlossaryImportStrategy = "skip" | "replace" | "replace_all";
export interface GlossaryTermValues {
  source: string;
  translation: string;
  category: string;
  description: string;
  locked: boolean;
  accepted: boolean;
}
export interface GlossaryConflict {
  line: number;
  source: string;
  existing: Omit<GlossaryTermValues, "source">;
  incoming: Omit<GlossaryTermValues, "source">;
  fields: GlossaryField[];
  locked: boolean;
  action: "replace" | "keep";
}
export interface GlossaryImportReport {
  format: "json" | "csv" | "tbx";
  encoding: string;
  delimiter: string | null;
  /** CSV: the first row's cells, whether it is a header, and the column of each field. */
  columns: string[];
  header: boolean;
  mapping: Partial<Record<GlossaryField, number>>;
  strategy: GlossaryImportStrategy;
  counts: {
    terms: number;
    new: number;
    unchanged: number;
    conflicts: number;
    replaced: number;
    kept: number;
    duplicates: number;
    errors: number;
  };
  new: (GlossaryTermValues & { line: number })[];
  conflicts: GlossaryConflict[];
  duplicates: { line: number; source: string; first_line: number }[];
  errors: { line: number; message: string }[];
  truncated: boolean;
  applied: boolean;
  imported?: number;
  replaced?: number;
  skipped?: number;
}
export interface SharedGlossaryTerm extends GlossaryTermValues {
  id: string;
  glossary_id: string;
}
export interface SharedGlossary {
  id: string;
  name: string;
  description: string;
  source_language: string | null;
  target_language: string | null;
  created_at: number;
  updated_at: number;
  term_count: number;
  locked_count: number;
  series: { id: string; name: string }[];
  terms?: SharedGlossaryTerm[];
}
export interface EffectiveTermLevel {
  level: "book" | "series" | "shared";
  translation: string;
  locked: boolean;
  /** book | series_override | series_decision | volume | shared_glossary */
  origin: string;
  volume: number | null;
}
export interface EffectiveTerm extends EffectiveTermLevel {
  source: string;
  overridden: EffectiveTermLevel[];
}
