// src-ui/src/store/index.ts
//
// Zustand store. Single source of truth for the whole frontend.
// All API calls go through here — components never call api.ts directly.

import { create } from "zustand";
import { immer } from "zustand/middleware/immer";
import * as api from "../lib/api";
import type {
  AgentID,
  AgentState,
  AnySSEEvent,
  AppSettings,
  DatasetRow,
  JobConfig,
  LogLine,
  OutputFormat,
  PipelineState,
  PipelineStatus,
  ProjectSummary,
  RightPanel,
  RowStatus,
  Screen,
  SettingsTab,
  Toast,
} from "../types";

// ── State shape ───────────────────────────────────────────────────────────────

interface DatasetterState {
  // Navigation
  screen: Screen;
  rightPanel: RightPanel;
  settingsTab: SettingsTab;

  // Active job
  currentJobId: string | null;
  currentConfig: JobConfig | null;
  pipelineState: PipelineState | null;
  rows: DatasetRow[];
  logs: LogLine[];
  sseCleanup: (() => void) | null;

  // UI state
  selectedRowId: number | null;
  filterStatus: RowStatus | "all";
  searchQuery: string;
  sortCol: "id" | "category" | "status";
  sortDir: "asc" | "desc";

  // Projects
  projects: ProjectSummary[];
  projectsLoaded: boolean;

  // Settings
  settings: AppSettings | null;
  settingsLoaded: boolean;

  // Toasts
  toasts: Toast[];

  // Confirm dialog
  confirm: {
    message: string;
    detail?: string;
    confirmLabel?: string;
    danger?: boolean;
    onConfirm: () => void;
  } | null;

  // Agent detail modal
  agentDetailId: AgentID | null;

  // ── Actions ─────────────────────────────────────────────────────────────────

  setScreen: (s: Screen) => void;
  setRightPanel: (p: RightPanel) => void;
  setSettingsTab: (t: SettingsTab) => void;

  // Job lifecycle
  launchJob: (config: JobConfig) => Promise<void>;
  pauseJob: () => Promise<void>;
  resumeJob: () => Promise<void>;
  cancelJob: () => Promise<void>;
  openJob: (jobId: string) => Promise<void>;

  // Row operations
  selectRow: (id: number | null) => void;
  editRow: (rowId: number, fields: Record<string, unknown>) => Promise<void>;
  acceptRow: (rowId: number) => Promise<void>;
  flagRow: (rowId: number, reason?: string) => Promise<void>;
  regenerateRow: (rowId: number) => Promise<void>;
  exportJob: (format: OutputFormat, path?: string) => Promise<api.ExportResponse>;

  // Table controls
  setFilter: (s: RowStatus | "all") => void;
  setSearch: (q: string) => void;
  setSort: (col: "id" | "category" | "status") => void;

  // Projects
  loadProjects: () => Promise<void>;
  deleteProject: (jobId: string) => Promise<void>;

  // Settings
  loadSettings: () => Promise<void>;
  saveSettings: (s: AppSettings) => Promise<void>;
  rescanHardware: () => Promise<void>;

  // Toast
  addToast: (level: Toast["level"], title: string, message: string) => void;
  removeToast: (id: string) => void;

  // Confirm dialog
  showConfirm: (opts: DatasetterState["confirm"]) => void;
  hideConfirm: () => void;

  // Agent detail
  openAgentDetail: (id: AgentID) => void;
  closeAgentDetail: () => void;

  // SSE handler
  _handleSSE: (event: AnySSEEvent) => void;
}

// ── Store ─────────────────────────────────────────────────────────────────────

export const useStore = create<DatasetterState>()(
  immer((set, get) => ({
    // ── Initial state ──────────────────────────────────────────────────────────
    screen:        "home",
    rightPanel:    "log",
    settingsTab:   "Pipeline",
    currentJobId:  null,
    currentConfig: null,
    pipelineState: null,
    rows:          [],
    logs:          [],
    sseCleanup:    null,
    selectedRowId: null,
    filterStatus:  "all",
    searchQuery:   "",
    sortCol:       "id",
    sortDir:       "asc",
    projects:      [],
    projectsLoaded: false,
    settings:      null,
    settingsLoaded: false,
    toasts:        [],
    confirm:       null,
    agentDetailId: null,

    // ── Navigation ─────────────────────────────────────────────────────────────
    setScreen:     (s) => set((st) => { st.screen = s; }),
    setRightPanel: (p) => set((st) => { st.rightPanel = p; }),
    setSettingsTab:(t) => set((st) => { st.settingsTab = t; }),

    // ── Job lifecycle ──────────────────────────────────────────────────────────

    launchJob: async (config) => {
      // Close any existing SSE connection
      get().sseCleanup?.();

      set((st) => {
        st.currentJobId  = config.job_id;
        st.currentConfig = config;
        st.pipelineState = null;
        st.rows          = [];
        st.logs          = [];
        st.selectedRowId = null;
        st.screen        = "workspace";
        st.sseCleanup    = null;
      });

      try {
        const resp = await api.startJob(config);

        // Connect SSE
        const cleanup = await api.connectJobEvents(
          resp.job_id,
          get()._handleSSE,
          () => {
            // Stream closed — mark complete if still running
            set((st) => {
              if (st.pipelineState?.status === "running") {
                if (st.pipelineState) st.pipelineState.status = "complete";
              }
            });
          }
        );

        set((st) => {
          st.sseCleanup = cleanup;
        });

        get().addToast("info", "Pipeline launched", resp.message);
      } catch (e: unknown) {
        const msg = e instanceof Error ? e.message : String(e);
        get().addToast("error", "Launch failed", msg);
        throw e;
      }
    },

    pauseJob: async () => {
      const id = get().currentJobId;
      if (!id) return;
      await api.pauseJob(id);
      set((st) => {
        if (st.pipelineState) st.pipelineState.status = "paused";
      });
    },

    resumeJob: async () => {
      const id = get().currentJobId;
      if (!id) return;
      await api.resumeJob(id);
      set((st) => {
        if (st.pipelineState) st.pipelineState.status = "running";
      });
    },

    cancelJob: async () => {
      const id = get().currentJobId;
      if (!id) return;
      get().sseCleanup?.();
      await api.cancelJob(id);
      set((st) => {
        if (st.pipelineState) st.pipelineState.status = "cancelled";
        st.sseCleanup = null;
      });
      get().addToast("warning", "Pipeline cancelled", "Job was stopped.");
    },

    openJob: async (jobId) => {
      get().sseCleanup?.();
      set((st) => {
        st.currentJobId  = jobId;
        st.rows          = [];
        st.logs          = [];
        st.pipelineState = null;
        st.screen        = "workspace";
      });
      try {
        const { config } = await api.getProject(jobId);
        set((st) => { st.currentConfig = config; });

        // Paginate row loading — fetch in batches of 500 to handle large datasets
        let offset = 0;
        const PAGE = 500;
        let done   = false;
        while (!done) {
          const rowsResp = await api.getRows(jobId, { limit: PAGE, offset });
          set((st) => {
            for (const row of rowsResp.rows) {
              const idx = st.rows.findIndex((r) => r.id === row.id);
              if (idx !== -1) st.rows[idx] = row;
              else st.rows.push(row);
            }
          });
          offset += rowsResp.rows.length;
          // Stop when we've loaded all rows or got a partial page
          done = rowsResp.rows.length < PAGE || offset >= rowsResp.total;
          // Cap at 5000 in memory — very large datasets are browsed via filter/search
          if (offset >= 5000) break;
        }
      } catch (e: unknown) {
        const msg = e instanceof Error ? e.message : String(e);
        get().addToast("error", "Could not open project", msg);
      }
    },

    // ── Row operations ─────────────────────────────────────────────────────────

    selectRow: (id) => set((st) => { st.selectedRowId = id; }),

    editRow: async (rowId, fields) => {
      const id = get().currentJobId;
      if (!id) return;
      const { row } = await api.editRow(id, rowId, fields);
      set((st) => {
        const idx = st.rows.findIndex((r) => r.id === rowId);
        if (idx !== -1) st.rows[idx] = row;
      });
    },

    acceptRow: async (rowId) => {
      const id = get().currentJobId;
      if (!id) return;
      await api.acceptRow(id, rowId);
      set((st) => {
        const idx = st.rows.findIndex((r) => r.id === rowId);
        if (idx !== -1) {
          st.rows[idx].status = "ok";
          st.rows[idx].errors = [];
        }
      });
    },

    flagRow: async (rowId, reason = "Manual flag.") => {
      const id = get().currentJobId;
      if (!id) return;
      await api.flagRow(id, rowId, reason);
      set((st) => {
        const idx = st.rows.findIndex((r) => r.id === rowId);
        if (idx !== -1) st.rows[idx].status = "error";
      });
    },

    regenerateRow: async (rowId) => {
      const id = get().currentJobId;
      if (!id) return;
      set((st) => {
        const idx = st.rows.findIndex((r) => r.id === rowId);
        if (idx !== -1) st.rows[idx].status = "fixing";
      });
      await api.regenerateRow(id, rowId);
    },

    exportJob: async (format, path) => {
      const id = get().currentJobId;
      if (!id) throw new Error("No active job");
      const resp = await api.exportJob({
        job_id: id,
        output_format: format,
        include_statuses: ["ok"],
        output_path: path ?? null,
      });
      get().addToast("success", "Export complete", `${resp.row_count} rows saved to ${resp.path}`);
      return resp;
    },

    // ── Table controls ─────────────────────────────────────────────────────────

    setFilter: (s) => set((st) => { st.filterStatus = s; }),
    setSearch: (q) => set((st) => { st.searchQuery = q; }),
    setSort: (col) => set((st) => {
      if (st.sortCol === col) {
        st.sortDir = st.sortDir === "asc" ? "desc" : "asc";
      } else {
        st.sortCol = col;
        st.sortDir = "asc";
      }
    }),

    // ── Projects ───────────────────────────────────────────────────────────────

    loadProjects: async () => {
      const projects = await api.getProjects();
      set((st) => { st.projects = projects; st.projectsLoaded = true; });
    },

    deleteProject: async (jobId) => {
      await api.deleteProject(jobId);
      set((st) => { st.projects = st.projects.filter((p) => p.job_id !== jobId); });
    },

    // ── Settings ───────────────────────────────────────────────────────────────

    loadSettings: async () => {
      const s = await api.getSettings();
      set((st) => { st.settings = s; st.settingsLoaded = true; });
    },

    saveSettings: async (s) => {
      await api.saveSettings(s);
      set((st) => { st.settings = s; });
      get().addToast("success", "Settings saved", "");
    },

    rescanHardware: async () => {
      const profile = await api.rescanHardware();
      set((st) => { if (st.settings) st.settings.hardware = profile; });
      get().addToast("success", "Hardware rescanned", `Tier: ${profile.tier}`);
    },

    // ── Toasts ─────────────────────────────────────────────────────────────────

    addToast: (level, title, message) => {
      const id = Math.random().toString(36).slice(2);
      set((st) => { st.toasts.push({ id, level, title, message }); });
      setTimeout(() => get().removeToast(id), 5000);

      // Also show native notification for important events
      if (level === "success" || level === "error") {
        api.showNativeNotification(title, message).catch(() => {});
      }
    },

    removeToast: (id) => set((st) => {
      st.toasts = st.toasts.filter((t) => t.id !== id);
    }),

    // ── Confirm ────────────────────────────────────────────────────────────────

    showConfirm: (opts) => set((st) => { st.confirm = opts; }),
    hideConfirm: ()     => set((st) => { st.confirm = null; }),

    // ── Agent detail ───────────────────────────────────────────────────────────

    openAgentDetail:  (id) => set((st) => { st.agentDetailId = id; }),
    closeAgentDetail: ()   => set((st) => { st.agentDetailId = null; }),

    // ── SSE handler ────────────────────────────────────────────────────────────

    _handleSSE: (event) => {
      // notification and error events are handled after the immer block
      // because they call addToast which can't run inside immer
      if (event.event === "notification" || event.event === "error") {
        if (event.event === "notification") {
          get().addToast(event.level, event.title, event.message);
        } else {
          get().addToast(
            "error",
            `Error${event.agent_id ? ` (${event.agent_id})` : ""}`,
            event.message
          );
        }
        return;
      }

      set((st) => {
        switch (event.event) {
          case "pipeline_status":
            st.pipelineState = event.state;
            break;

          case "agent_log":
            st.logs.push({
              agent_id:  event.agent_id,
              message:   event.message,
              timestamp: event.timestamp,
            });
            // Cap log history at 500 lines to prevent memory growth
            if (st.logs.length > 500) {
              st.logs.splice(0, st.logs.length - 500);
            }
            break;

          case "agent_status": {
            if (!st.pipelineState) break;
            if (!st.pipelineState.agent_states) st.pipelineState.agent_states = {};
            const existing = st.pipelineState.agent_states[event.agent_id] ?? {
              agent_id:       event.agent_id,
              status:         "idle",
              started_at:     null,
              finished_at:    null,
              current_task:   "",
              rows_processed: 0,
              error_message:  null,
              model_used:     null,
              tokens_used:    0,
            } as AgentState;
            st.pipelineState.agent_states[event.agent_id] = {
              ...existing,
              status:       event.status,
              current_task: event.current_task,
              model_used:   event.model_used,
            };
            break;
          }

          case "row_update": {
            const idx = st.rows.findIndex((r) => r.id === event.row.id);
            if (idx !== -1) {
              st.rows[idx] = event.row;
            } else {
              st.rows.push(event.row);
            }
            break;
          }

          case "rows_batch":
            for (const row of event.rows) {
              const idx = st.rows.findIndex((r) => r.id === row.id);
              if (idx !== -1) {
                st.rows[idx] = row;
              } else {
                st.rows.push(row);
              }
            }
            break;
        }
      });
    },
  }))
);

// ── Derived selectors ─────────────────────────────────────────────────────────

export function selectFilteredRows(state: DatasetterState): DatasetRow[] {
  let rows = state.rows;

  if (state.filterStatus !== "all") {
    rows = rows.filter((r) => r.status === state.filterStatus);
  }

  if (state.searchQuery.trim()) {
    const q = state.searchQuery.toLowerCase();
    rows = rows.filter((r) =>
      r.category.toLowerCase().includes(q) ||
      Object.values(r.fields).some((v) =>
        String(v).toLowerCase().includes(q)
      )
    );
  }

  const col = state.sortCol;
  const dir = state.sortDir === "asc" ? 1 : -1;

  rows = [...rows].sort((a, b) => {
    let av: string | number, bv: string | number;
    if (col === "id") { av = a.id; bv = b.id; }
    else if (col === "category") { av = a.category; bv = b.category; }
    else { av = a.status; bv = b.status; }
    if (av < bv) return -1 * dir;
    if (av > bv) return 1 * dir;
    return 0;
  });

  return rows;
}

export function selectSelectedRow(state: DatasetterState): DatasetRow | null {
  if (state.selectedRowId === null) return null;
  return state.rows.find((r) => r.id === state.selectedRowId) ?? null;
}

export function selectRowStats(state: DatasetterState) {
  const total   = state.rows.length;
  const ok      = state.rows.filter((r) => r.status === "ok").length;
  const error   = state.rows.filter((r) => r.status === "error").length;
  const fixing  = state.rows.filter((r) => r.status === "fixing").length;
  const pending = state.rows.filter((r) => r.status === "pending").length;
  const manual  = state.rows.filter((r) => r.status === "manual").length;
  return { total, ok, error, fixing, pending, manual };
}

export function selectProgress(state: DatasetterState) {
  const ps = state.pipelineState;
  if (!ps || !ps.total_rows) return { pct: 0, verified: 0, total: 0 };
  return {
    pct:      Math.round((ps.verified_rows / ps.total_rows) * 100),
    verified: ps.verified_rows,
    total:    ps.total_rows,
  };
}
