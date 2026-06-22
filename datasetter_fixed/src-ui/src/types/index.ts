// src-ui/src/types/index.ts
// Mirrors core/models.py — keep in sync with backend.

// ── Enums ─────────────────────────────────────────────────────────────────────

export type AgentID =
  | "orchestrator"
  | "interpreter"
  | "analyser"
  | "researcher"
  | "generator"
  | "scripter"
  | "verifier"
  | "fixer";

export type AgentStatus = "idle" | "running" | "done" | "failed" | "skipped";

export type PipelineMode = "vibe" | "file" | "research" | "edit" | "minimal";

export type PipelineStatus =
  | "pending"
  | "running"
  | "paused"
  | "complete"
  | "failed"
  | "cancelled";

export type RowStatus = "pending" | "ok" | "error" | "fixing" | "manual";

export type ErrorType =
  | "semantic"
  | "logic"
  | "constraint"
  | "consistency"
  | "format"
  | "length";

export type InferenceMode = "cloud" | "local" | "auto";

export type OutputFormat =
  | "jsonl"
  | "csv"
  | "parquet"
  | "tsv"
  | "json"
  | "arrow"
  | "xml"
  | "xlsx";

export type VerifyMode = "batch" | "one_by_one";

export type NotificationLevel = "info" | "success" | "warning" | "error";

// ── Hardware ──────────────────────────────────────────────────────────────────

export interface GPUInfo {
  name: string;
  vram_gb: number;
  vendor: "nvidia" | "amd" | "intel" | "apple" | "unknown";
}

export interface HardwareProfile {
  gpu: GPUInfo | null;
  ram_gb: number;
  cpu_name: string;
  has_npu: boolean;
  npu_name: string | null;
  os: string;
  has_ollama: boolean;
  has_llama_cpp: boolean;
  has_mlx: boolean;
  has_onnx_runtime: boolean;
  tier: "low" | "mid" | "high" | "ultra";
}

// ── Models ────────────────────────────────────────────────────────────────────

export interface ModelAssignment {
  agent_id: AgentID;
  cloud_model: string | null;
  local_model: string | null;
  mode: InferenceMode;
  cloud_provider: string | null;
  local_engine: string | null;
  quantization: string | null;
}

export interface ModelConfig {
  assignments: Partial<Record<AgentID, ModelAssignment>>;
}

// ── Dataset row ───────────────────────────────────────────────────────────────

export interface VerifierError {
  row_id: number;
  error_type: ErrorType;
  field: string;
  description: string;
  fix_instruction: string;
  severity: "fatal" | "minor";
}

export interface DatasetRow {
  id: number;
  status: RowStatus;
  category: string;
  fields: Record<string, unknown>;
  errors: VerifierError[];
  fix_rounds: number;
  manually_edited: boolean;
  seed_id: number | null;
  batch_id: number | null;
}

// ── Job config ────────────────────────────────────────────────────────────────

export interface FieldConstraint {
  field: string;
  min_length: number | null;
  max_length: number | null;
  required: boolean;
  description: string;
  examples: string[];
  forbidden_patterns: string[];
}

export interface JobConfig {
  job_id: string;
  name: string;
  prompt: string;
  extra_context: string;
  negative_prompt: string;
  attached_files: string[];
  output_format: OutputFormat;
  use_case: string;
  total_rows: number;
  category_count: number | null;
  seed_count: number;
  language: string;
  field_constraints: FieldConstraint[];
  diversity_level: number;
  edge_case_coverage: "low" | "medium" | "high";
  verify_mode: VerifyMode;
  batch_size: number;
  strictness: number;
  error_halt_threshold: number;
  auto_fix: boolean;
  max_fix_rounds: number;
  check_semantic: boolean;
  check_logic: boolean;
  check_constraints: boolean;
  check_consistency: boolean;
  check_format: boolean;
  check_length: boolean;
  kv_cache_clear_interval: number;
  model_overrides: Partial<Record<AgentID, ModelAssignment>>;
  pipeline_mode: PipelineMode | null;
}

// ── Seeds ─────────────────────────────────────────────────────────────────────

export interface SeedExample {
  id: number;
  category: string;
  fields: Record<string, unknown>;
  is_edge_case: boolean;
  edge_case_type: string | null;
}

export interface SeedPack {
  seeds: SeedExample[];
  categories: string[];
  category_targets: Record<string, number>;
  generation_spec: Record<string, unknown>;
  blueprint: string;
}

// ── Pipeline state ────────────────────────────────────────────────────────────

export interface AgentState {
  agent_id: AgentID;
  status: AgentStatus;
  started_at: number | null;
  finished_at: number | null;
  current_task: string;
  rows_processed: number;
  error_message: string | null;
  model_used: string | null;
  tokens_used: number;
}

export interface PipelineState {
  job_id: string;
  status: PipelineStatus;
  pipeline_mode: PipelineMode | null;
  active_agents: AgentID[];
  agent_states: Partial<Record<AgentID, AgentState>>;
  total_rows: number;
  generated_rows: number;
  verified_rows: number;
  error_rows: number;
  fixed_rows: number;
  started_at: number | null;
  estimated_finish: number | null;
  current_category: string | null;
  current_batch: number | null;
  total_batches: number | null;
}

// ── Events (SSE) ──────────────────────────────────────────────────────────────

export type EventType =
  | "pipeline_status"
  | "agent_log"
  | "agent_status"
  | "row_update"
  | "rows_batch"
  | "notification"
  | "error"
  | "complete";

export interface AgentLogEvent {
  event: "agent_log";
  job_id: string;
  agent_id: AgentID;
  message: string;
  timestamp: number;
}

export interface AgentStatusEvent {
  event: "agent_status";
  job_id: string;
  agent_id: AgentID;
  status: AgentStatus;
  model_used: string | null;
  current_task: string;
}

export interface RowUpdateEvent {
  event: "row_update";
  job_id: string;
  row: DatasetRow;
}

export interface RowsBatchEvent {
  event: "rows_batch";
  job_id: string;
  rows: DatasetRow[];
}

export interface PipelineStatusEvent {
  event: "pipeline_status";
  state: PipelineState;
}

export interface NotificationEvent {
  event: "notification";
  level: NotificationLevel;
  title: string;
  message: string;
}

export interface ErrorEvent {
  event: "error";
  job_id: string;
  agent_id: AgentID | null;
  message: string;
  recoverable: boolean;
}

export type AnySSEEvent =
  | AgentLogEvent
  | AgentStatusEvent
  | RowUpdateEvent
  | RowsBatchEvent
  | PipelineStatusEvent
  | NotificationEvent
  | ErrorEvent;

// ── API request/response ──────────────────────────────────────────────────────

export interface StartJobResponse {
  job_id: string;
  pipeline_mode: PipelineMode;
  active_agents: AgentID[];
  message: string;
}

export interface ExportRequest {
  job_id: string;
  output_format: OutputFormat;
  include_statuses: RowStatus[];
  output_path: string | null;
}

export interface ExportResponse {
  path: string;
  row_count: number;
  format: OutputFormat;
}

export interface ProjectSummary {
  job_id: string;
  name: string;
  status: PipelineStatus;
  pipeline_mode: PipelineMode;
  total_rows: number;
  verified_rows: number;
  error_rows: number;
  output_format: OutputFormat;
  created_at: number;
  updated_at: number;
}

export interface RowsResponse {
  total: number;
  offset: number;
  limit: number;
  rows: DatasetRow[];
}

// ── Settings ──────────────────────────────────────────────────────────────────

export interface APIKeyConfig {
  anthropic: string | null;
  google: string | null;
  deepinfra: string | null;
  featherless: string | null;
  groq: string | null;
  sambanova: string | null;
  fireworks: string | null;
  novita: string | null;
  siliconflow: string | null;
  runpod: string | null;
  custom_endpoints: Array<{
    url: string;
    label: string;
    api_key: string;
    model?: string;
  }>;
}

export interface AppSettings {
  api_keys: APIKeyConfig;
  agent_models: ModelConfig;  // renamed from model_config to avoid Pydantic v2 conflict
  hardware: HardwareProfile | null;
  default_rows: number;
  default_seed_count: number;
  default_language: string;
  default_format: OutputFormat;
  default_diversity: number;
  default_edge_case_coverage: string;
  default_negative_prompt: string;
  default_verify_mode: VerifyMode;
  default_batch_size: number;
  default_strictness: number;
  default_auto_fix: boolean;
  default_max_fix_rounds: number;
  default_kv_cache_clear_interval: number;
  default_max_retries: number;
  default_retry_delay: number;
  background_mode: boolean;
  push_notifications: boolean;
  notify_batch_complete: boolean;
  notify_errors: boolean;
  notify_pipeline_done: boolean;
  storage_path: string;
  export_path: string;
}

// ── UI-only types ─────────────────────────────────────────────────────────────

export interface AgentMeta {
  id: AgentID;
  label: string;
  short: string;
  color: string;
  desc: string;
}

export interface LogLine {
  agent_id: AgentID;
  message: string;
  timestamp: number;
}

export interface Toast {
  id: string;
  level: NotificationLevel;
  title: string;
  message: string;
}

export type Screen = "home" | "new" | "workspace" | "settings" | "history";
export type RightPanel = "log" | "editor" | "analytics" | "seeds" | "preview";
export type SettingsTab =
  | "Pipeline"
  | "Models"
  | "Verification"
  | "Generation"
  | "APIs"
  | "System";
