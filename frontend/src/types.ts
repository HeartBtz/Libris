export interface User {
  id: string;
  username: string;
  admin: boolean;
}
export interface Stats {
  reviewed_segments?: number;
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
  source_language: string;
  target_language: string;
  provider_id: string | null;
  quality: string;
  context_backend: string;
  instructions: string;
  status: string;
  stats: Stats;
  updated_at: number;
  book_info: {
    words: number;
    images: number;
    size: number;
    validation?: unknown;
  };
  bible: Record<string, unknown>;
}
export interface Provider {
  kind: "openai" | "openai_responses" | "codex_chatgpt";
  created_at?: number;
  id: string;
  name: string;
  base_url: string;
  model: string;
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
export type Run = (task: () => Promise<void>) => Promise<void>;
