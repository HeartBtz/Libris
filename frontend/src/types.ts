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
  /** Present only on servers that support reusing validated translations. */
  translation_memory?: boolean;
  status: string;
  stats: Stats;
  updated_at: number;
  book_info: {
    words: number;
    images: number;
    size: number;
    validation?: unknown;
  };
  /** Only the project detail carries the Book Bible; the list omits it. */
  bible?: Record<string, unknown>;
  progress?: ProjectProgress;
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
