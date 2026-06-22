// src-ui/src/lib/api.ts
//
// All communication with the Python sidecar goes through here.
// On startup we call Tauri to get the sidecar port, then use
// that for all subsequent fetch() calls.
//
// SSE events are handled via EventSource.
// File dialogs go through Tauri commands.

import { invoke } from "@tauri-apps/api/core";
import type {
  AnySSEEvent,
  AppSettings,
  DatasetRow,
  ExportRequest,
  ExportResponse,
  HardwareProfile,
  JobConfig,
  ProjectSummary,
  RowsResponse,
  StartJobResponse,
} from "../types";

// ── Port resolution ───────────────────────────────────────────────────────────

let _port: number | null = null;

async function getPort(): Promise<number> {
  if (_port !== null) return _port;
  try {
    _port = await invoke<number>("get_sidecar_port");
  } catch {
    // Fallback for browser-based dev (no Tauri)
    _port = 57423;
  }
  return _port;
}

async function url(path: string): Promise<string> {
  const port = await getPort();
  return `http://127.0.0.1:${port}${path}`;
}

// ── Base fetch ────────────────────────────────────────────────────────────────

async function api<T>(
  path: string,
  options: RequestInit = {}
): Promise<T> {
  const endpoint = await url(path);
  const res = await fetch(endpoint, {
    headers: { "Content-Type": "application/json", ...options.headers },
    ...options,
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`API ${options.method ?? "GET"} ${path} → ${res.status}: ${body}`);
  }
  // 204 No Content
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

// ── Health ────────────────────────────────────────────────────────────────────

export async function checkHealth(): Promise<boolean> {
  try {
    await api("/health");
    return true;
  } catch {
    return false;
  }
}

// ── Jobs ──────────────────────────────────────────────────────────────────────

export async function startJob(config: JobConfig): Promise<StartJobResponse> {
  return api<StartJobResponse>("/jobs/start", {
    method: "POST",
    body: JSON.stringify({ config }),
  });
}

export async function pauseJob(jobId: string): Promise<void> {
  return api(`/jobs/${jobId}/pause`, { method: "POST" });
}

export async function resumeJob(jobId: string): Promise<void> {
  return api(`/jobs/${jobId}/resume`, { method: "POST" });
}

export async function cancelJob(jobId: string): Promise<void> {
  return api(`/jobs/${jobId}/cancel`, { method: "POST" });
}

// ── SSE ───────────────────────────────────────────────────────────────────────

export type SSEHandler = (event: AnySSEEvent) => void;
export type SSECloseHandler = () => void;

// src-ui/src/lib/api.ts SSE fix — errors are now logged
export async function connectJobEvents(
  jobId: string,
  onEvent: SSEHandler,
  onClose?: SSECloseHandler
): Promise<() => void> {
  const endpoint = await url(`/jobs/${jobId}/events`);
  const es = new EventSource(endpoint);

  es.onmessage = (e) => {
    if (!e.data || e.data === "{}" || e.data.startsWith(":")) return;
    try {
      const parsed: AnySSEEvent = JSON.parse(e.data);
      onEvent(parsed);
    } catch (err) {
      console.warn("[SSE] Failed to parse event:", e.data, err);
    }
  };

  es.addEventListener("close", () => {
    es.close();
    onClose?.();
  });

  es.onerror = (e) => {
    // EventSource auto-reconnects on transient errors.
    // Log so developers can see recurring failures without crashing the app.
    console.warn("[SSE] Connection error (will auto-reconnect):", e);
  };

  return () => {
    es.close();
    onClose?.();
  };
}

// ── Rows ──────────────────────────────────────────────────────────────────────

export async function getRows(
  jobId: string,
  opts: { status?: string; category?: string; offset?: number; limit?: number } = {}
): Promise<RowsResponse> {
  const params = new URLSearchParams();
  if (opts.status)   params.set("status",   opts.status);
  if (opts.category) params.set("category", opts.category);
  if (opts.offset !== undefined) params.set("offset", String(opts.offset));
  if (opts.limit  !== undefined) params.set("limit",  String(opts.limit));
  const qs = params.toString() ? `?${params}` : "";
  return api<RowsResponse>(`/jobs/${jobId}/rows${qs}`);
}

export async function editRow(
  jobId: string,
  rowId: number,
  fields: Record<string, unknown>
): Promise<{ status: string; row: DatasetRow }> {
  return api(`/jobs/${jobId}/rows/${rowId}`, {
    method: "PATCH",
    body: JSON.stringify({ job_id: jobId, row_id: rowId, fields }),
  });
}

export async function acceptRow(jobId: string, rowId: number): Promise<void> {
  return api(`/jobs/${jobId}/rows/${rowId}/accept`, { method: "POST" });
}

export async function flagRow(
  jobId: string,
  rowId: number,
  reason: string
): Promise<void> {
  return api(`/jobs/${jobId}/rows/${rowId}/flag`, {
    method: "POST",
    body: JSON.stringify({ job_id: jobId, row_id: rowId, reason }),
  });
}

export async function regenerateRow(
  jobId: string,
  rowId: number,
  agentId?: string
): Promise<void> {
  return api(`/jobs/${jobId}/rows/${rowId}/regenerate`, {
    method: "POST",
    body: JSON.stringify({ job_id: jobId, row_id: rowId, agent_id: agentId ?? null }),
  });
}

// ── Export ────────────────────────────────────────────────────────────────────

export async function exportJob(req: ExportRequest): Promise<ExportResponse> {
  return api<ExportResponse>(`/jobs/${req.job_id}/export`, {
    method: "POST",
    body: JSON.stringify(req),
  });
}

// ── Projects ──────────────────────────────────────────────────────────────────

export async function getProjects(): Promise<ProjectSummary[]> {
  return api<ProjectSummary[]>("/projects");
}

export async function getProject(
  jobId: string
): Promise<{ config: JobConfig; state: unknown }> {
  return api(`/projects/${jobId}`);
}

export async function deleteProject(jobId: string): Promise<void> {
  return api(`/projects/${jobId}`, { method: "DELETE" });
}

// ── Settings ──────────────────────────────────────────────────────────────────

export async function getSettings(): Promise<AppSettings> {
  return api<AppSettings>("/settings");
}

export async function saveSettings(settings: AppSettings): Promise<void> {
  return api("/settings", {
    method: "PUT",
    body: JSON.stringify(settings),
  });
}

export async function getTokenUsage(jobId: string): Promise<{
  job_id: string;
  summary: string;
  totals: { prompt: number; completion: number; cached: number; total: number };
  per_agent: Record<string, { prompt: number; completion: number; cached: number }>;
  cache_hit_rate: number;
} | null> {
  try {
    return await api(`/jobs/${jobId}/tokens`);
  } catch {
    return null;
  }
}

export async function getJobSeeds(jobId: string): Promise<{
  seeds: import("../types").SeedExample[];
  categories: string[];
  category_targets: Record<string, number>;
  blueprint: string;
} | null> {
  try {
    return await api(`/jobs/${jobId}/seeds`);
  } catch {
    return null;
  }
}

export async function getJobBlueprint(jobId: string): Promise<string | null> {
  try {
    const data = await api<{ blueprint: string }>(`/jobs/${jobId}/blueprint`);
    return data.blueprint;
  } catch {
    return null;
  }
}

export async function reconfigureModels(): Promise<{
  status: string;
  assignments: Record<string, unknown>;
}> {
  return api("/settings/reconfigure", { method: "POST" });
}

export async function rescanHardware(): Promise<HardwareProfile> {
  return api<HardwareProfile>("/settings/scan-hardware", { method: "POST" });
}

export async function getHardware(): Promise<HardwareProfile> {
  return api<HardwareProfile>("/settings/hardware");
}

// ── Tauri native commands ─────────────────────────────────────────────────────

export interface FileDialogResult {
  paths: string[];
  cancelled: boolean;
}

export interface FileFilter {
  name: string;
  extensions: string[];
}

export async function openFileDialog(opts: {
  title?: string;
  multiple?: boolean;
  filters?: FileFilter[];
}): Promise<FileDialogResult> {
  try {
    return await invoke<FileDialogResult>("open_file_dialog", opts);
  } catch {
    // Browser fallback — not supported
    return { paths: [], cancelled: true };
  }
}

export async function openFolderDialog(opts: {
  title?: string;
}): Promise<FileDialogResult> {
  try {
    return await invoke<FileDialogResult>("open_folder_dialog", opts);
  } catch {
    return { paths: [], cancelled: true };
  }
}

export async function showNativeNotification(
  title: string,
  body: string
): Promise<void> {
  try {
    await invoke("show_notification", { title, body });
  } catch {
    // Fallback to browser notification if available
    if ("Notification" in window && Notification.permission === "granted") {
      new Notification(title, { body });
    }
  }
}

export async function openPath(path: string): Promise<void> {
  try {
    await invoke("open_path", { path });
  } catch (e) {
    console.warn("open_path failed:", e);
  }
}

export async function getPlatform(): Promise<string> {
  try {
    return await invoke<string>("get_platform");
  } catch {
    return "unknown";
  }
}

// ── Utilities ─────────────────────────────────────────────────────────────────

/** Generate a UUID v4 without any dependency. */
export function uuid(): string {
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    return (c === "x" ? r : (r & 0x3) | 0x8).toString(16);
  });
}

/** Default JobConfig with sensible values. */
export function defaultJobConfig(overrides: Partial<JobConfig> = {}): JobConfig {
  return {
    job_id: uuid(),
    name: "Untitled Dataset",
    prompt: "",
    extra_context: "",
    negative_prompt: "",
    attached_files: [],
    output_format: "jsonl",
    use_case: "",
    total_rows: 500,
    category_count: null,
    seed_count: 50,
    language: "English",
    field_constraints: [],
    diversity_level: 4,
    edge_case_coverage: "high",
    verify_mode: "batch",
    batch_size: 50,
    strictness: 3,
    error_halt_threshold: 0.1,
    auto_fix: true,
    max_fix_rounds: 3,
    check_semantic: true,
    check_logic: true,
    check_constraints: true,
    check_consistency: true,
    check_format: true,
    check_length: true,
    kv_cache_clear_interval: 50,
    model_overrides: {},
    pipeline_mode: null,
    ...overrides,
  };
}
