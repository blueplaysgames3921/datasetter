// src-ui/src/screens/WorkspaceScreen.tsx
//
// The main working view. Three-column layout:
//   Left  — agent sidebar + dataset stats
//   Center — dataset table (filterable, searchable, sortable)
//   Right — tabbed panel: Pipeline Log | Row Editor | Analytics | Seeds | Preview

import React, { useCallback, useRef, useState } from "react";
import {
  AgentBadge, Btn, FieldInput, FieldTextarea, StatusDot,
} from "../components";
import { openPath } from "../lib/api";
import { AGENT_META, getAgent } from "../lib/agents";
import {
  useFilteredRows,
  usePipelineStatus,
  useRowStats,
  useSelectedRow,
} from "../hooks";
import { useStore } from "../store";
import type { AgentID, DatasetRow, OutputFormat, RightPanel } from "../types";

// ── Status helpers ────────────────────────────────────────────────────────────

const STATUS_COLOR: Record<string, string> = {
  ok: "var(--ok)", error: "var(--error)",
  fixing: "var(--fixing)", pending: "var(--pending)", manual: "#a78bfa",
};
const STATUS_LABEL: Record<string, string> = {
  ok: "OK", error: "ERR", fixing: "FIX", pending: "·", manual: "MAN",
};

// ── Left sidebar ──────────────────────────────────────────────────────────────

function AgentSidebar() {
  const { pipelineState, openAgentDetail } = useStore();
  const stats = useRowStats();
  const { pct, verified, total } = usePipelineStatus();

  return (
    <div style={{
      width: 192, borderRight: "1px solid var(--border-dim)",
      display: "flex", flexDirection: "column", flexShrink: 0,
      background: "#040810", overflowY: "auto",
    }}>
      <div style={{ padding: "10px 13px 5px", fontSize: 9, fontWeight: 700, letterSpacing: ".15em", color: "var(--text-faint)" }}>
        AGENTS
      </div>

      {AGENT_META.map((a) => {
        const agState = pipelineState?.agent_states?.[a.id];
        const status  = agState?.status ?? "idle";
        const isRunning = status === "running";

        return (
          <div
            key={a.id}
            onClick={() => openAgentDetail(a.id)}
            style={{
              display: "flex", alignItems: "center", gap: 8,
              padding: "6px 11px", borderBottom: "1px solid #090f1c",
              cursor: "pointer", transition: "background .1s",
            }}
            onMouseEnter={(e) => (e.currentTarget.style.background = "var(--bg-surface)")}
            onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
          >
            <div style={{
              width: 30, height: 30, borderRadius: 5, flexShrink: 0,
              display: "flex", alignItems: "center", justifyContent: "center",
              background: status === "idle" ? "#090f1c" : `${a.color}12`,
              border: `1px solid ${status === "idle" ? "#162030" : a.color}`,
              fontSize: 9, fontWeight: 700, color: status === "idle" ? "var(--text-faint)" : a.color,
              fontFamily: "'JetBrains Mono', monospace",
            }}>
              {a.short}
            </div>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{
                fontSize: 11, fontWeight: 600,
                color: status === "idle" ? "var(--text-faint)" : "var(--text-muted)",
                overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
              }}>
                {a.label}
              </div>
              <div
                style={{
                  fontSize: 9, fontWeight: 700, letterSpacing: ".06em",
                  color: isRunning ? a.color : "var(--text-faint)",
                }}
                className={isRunning ? "pulse" : ""}
              >
                {isRunning ? "● RUNNING" : status === "done" ? "DONE" : status === "failed" ? "FAILED" : "IDLE"}
              </div>
            </div>
          </div>
        );
      })}

      {/* Dataset stats */}
      <div style={{ padding: "10px 13px 5px", fontSize: 9, fontWeight: 700, letterSpacing: ".15em", color: "var(--text-faint)", marginTop: 6 }}>
        DATASET
      </div>
      {([
        ["Total",    total,        "var(--text-dim)"],
        ["OK",       stats.ok,     "var(--ok)"],
        ["Errors",   stats.error,  "var(--error)"],
        ["Fixing",   stats.fixing, "var(--fixing)"],
        ["Pending",  stats.pending,"var(--text-faint)"],
        ["Manual",   stats.manual, "#a78bfa"],
        ["Verified", verified,     "#60c8f0"],
      ] as [string, number, string][]).map(([k, v, c]) => (
        <div key={k} style={{ display: "flex", justifyContent: "space-between", padding: "3px 13px", fontSize: 11 }}>
          <span style={{ color: "var(--text-faint)" }}>{k}</span>
          <span className="mono" style={{ color: c, fontSize: 11 }}>{v}</span>
        </div>
      ))}

      {/* Progress bar */}
      <div style={{ padding: "10px 13px 12px", marginTop: 2 }}>
        <div style={{ height: 3, background: "var(--bg-surface)", borderRadius: 2, overflow: "hidden" }}>
          <div style={{
            width: `${pct}%`, height: "100%",
            background: "linear-gradient(90deg, var(--accent), #f97316)",
            transition: "width .4s ease",
          }} />
        </div>
        <div className="mono" style={{ fontSize: 9, color: "var(--text-faint)", marginTop: 4, textAlign: "right" }}>
          {pct}% verified
        </div>
      </div>
    </div>
  );
}

// ── Center: Dataset table ─────────────────────────────────────────────────────

function DatasetTable() {
  const {
    filterStatus, setFilter, searchQuery, setSearch,
    sortCol, sortDir, setSort,
    selectedRowId, selectRow, setRightPanel,
    showConfirm, exportJob, currentJobId, rows,
  } = useStore();

  const filtered = useFilteredRows();
  const stats    = useRowStats();

  const sortIcon = (col: string) =>
    sortCol === col ? (sortDir === "asc" ? " ↑" : " ↓") : "";

  const handleExport = () => {
    if (!currentJobId) return;
    showConfirm({
      message: "Export dataset?",
      detail: `Export ${stats.ok} verified rows as JSONL?`,
      confirmLabel: "Export",
      onConfirm: async () => {
        try {
          const resp = await exportJob("jsonl" as OutputFormat);
          openPath(resp.path).catch(() => {});
        } catch (e: unknown) {
          console.error(e);
        }
      },
    });
  };

  const handleAddRow = () => {
    if (!currentJobId) return;
    // Build a blank row with the same field schema as existing rows
    const firstRow = rows[0];
    const blankFields = firstRow
      ? Object.fromEntries(Object.keys(firstRow.fields).map((k) => [k, ""]))
      : { instruction: "", response: "" };
    const newId = rows.length > 0 ? Math.max(...rows.map((r) => r.id)) + 1 : 0;
    const newRow: DatasetRow = {
      id: newId,
      status: "manual",
      category: firstRow?.category ?? "General",
      fields: blankFields,
      errors: [],
      fix_rounds: 0,
      manually_edited: true,
      seed_id: null,
      batch_id: null,
    };
    // Optimistically add to local store, then sync to backend
    useStore.setState((st) => ({ rows: [...st.rows, newRow] }));
    // Select and open editor immediately
    selectRow(newId);
    setRightPanel("editor");
    // Persist to backend
    import("../lib/api").then(({ editRow }) =>
      editRow(currentJobId, newId, blankFields).catch((e) =>
        console.error("Failed to persist new row:", e)
      )
    );
  };

  return (
    <div style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden", minWidth: 0 }}>
      {/* Toolbar */}
      <div style={{
        display: "flex", alignItems: "center", gap: 6,
        padding: "6px 11px", borderBottom: "1px solid var(--border-dim)",
        background: "#040810", flexShrink: 0, flexWrap: "wrap",
      }}>
        <span className="mono" style={{ fontSize: 9, color: "var(--text-faint)", letterSpacing: ".1em", marginRight: 2 }}>
          FILTER
        </span>
        {(["all", "ok", "error", "fixing", "pending", "manual"] as const).map((f) => {
          const c   = f === "all" ? "var(--text-dim)" : STATUS_COLOR[f];
          const act = filterStatus === f;
          return (
            <button
              key={f}
              onClick={() => setFilter(f)}
              style={{
                padding: "2px 9px", borderRadius: 3, fontSize: 9,
                letterSpacing: ".07em", cursor: "pointer",
                fontFamily: "'Syne', sans-serif", fontWeight: 700,
                background: act ? (f === "all" ? "var(--border)" : `${c}15`) : "transparent",
                color: act ? (f === "all" ? "var(--text-muted)" : c) : "var(--text-faint)",
                border: `1px solid ${act ? (f === "all" ? "var(--border)" : c) : "var(--border-dim)"}`,
              }}
            >
              {f.toUpperCase()}
            </button>
          );
        })}

        <div style={{ width: 1, height: 14, background: "var(--border)", margin: "0 2px" }} />

        {/* Search */}
        <div style={{ position: "relative", flex: 1, minWidth: 120, maxWidth: 220 }}>
          <span style={{
            position: "absolute", left: 8, top: "50%", transform: "translateY(-50%)",
            fontSize: 11, color: "var(--text-faint)", pointerEvents: "none",
          }}>⌕</span>
          <input
            value={searchQuery}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search rows…"
            style={{
              width: "100%", background: "var(--bg-surface)",
              border: "1px solid var(--border)", borderRadius: 4,
              color: "var(--text)", padding: "3px 8px 3px 24px",
              fontFamily: "'JetBrains Mono', monospace", fontSize: 10,
              outline: "none", height: 24,
            }}
          />
        </div>

        <div style={{ flex: 1 }} />
        <Btn variant="info" onClick={handleAddRow}>+ Row</Btn>
        <Btn variant="success" onClick={handleExport}>↓ Export</Btn>
      </div>

      {/* Table header */}
      <div style={{
        display: "grid", gridTemplateColumns: "30px 80px 1fr 60px",
        gap: 8, padding: "5px 12px", borderBottom: "1px solid var(--border-dim)",
        fontSize: 9, fontWeight: 700, letterSpacing: ".12em",
        color: "var(--text-faint)", flexShrink: 0,
      }}>
        <div onClick={() => setSort("id")} style={{ cursor: "pointer", userSelect: "none" }}>#</div>
        <div onClick={() => setSort("category")} style={{ cursor: "pointer", userSelect: "none" }}>
          CAT{sortIcon("category")}
        </div>
        <div style={{ userSelect: "none" }}>CONTENT</div>
        <div onClick={() => setSort("status")} style={{ cursor: "pointer", userSelect: "none" }}>
          STATUS{sortIcon("status")}
        </div>
      </div>

      {/* Rows */}
      <div style={{ flex: 1, overflowY: "auto" }}>
        {filtered.length === 0 && (
          <div style={{ padding: 32, textAlign: "center", fontSize: 12, color: "var(--text-faint)" }}>
            {searchQuery ? "No rows match your search." : "No rows yet."}
          </div>
        )}
        {filtered.map((row) => {
          // Show first field value as preview
          const firstVal = Object.values(row.fields)[0];
          const preview  = firstVal != null ? String(firstVal) : "—";
          const isSelected = row.id === selectedRowId;

          return (
            <div
              key={row.id}
              onClick={() => { selectRow(row.id); setRightPanel("editor"); }}
              style={{
                display: "grid", gridTemplateColumns: "30px 80px 1fr 60px",
                gap: 8, alignItems: "center",
                padding: "7px 12px", borderBottom: "1px solid #090f1c",
                cursor: "pointer", transition: "background .1s",
                background: isSelected ? "var(--bg-overlay)" : "transparent",
                fontSize: 11,
              }}
              onMouseEnter={(e) => { if (!isSelected) e.currentTarget.style.background = "var(--bg-surface)"; }}
              onMouseLeave={(e) => { if (!isSelected) e.currentTarget.style.background = "transparent"; }}
            >
              <div className="mono" style={{ fontSize: 10, color: "var(--text-faint)" }}>{row.id}</div>
              <div style={{
                fontSize: 10, color: "#60c8f0", fontWeight: 600,
                overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
              }}>
                {row.category}
              </div>
              <div style={{
                fontSize: 11,
                color: row.status === "pending" ? "var(--text-faint)" : "var(--text-muted)",
                overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
              }}>
                {preview}
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
                <StatusDot status={row.status} pulse={row.status === "fixing"} />
                <span className="mono" style={{ fontSize: 9, color: STATUS_COLOR[row.status], fontWeight: 700 }}>
                  {STATUS_LABEL[row.status]}
                </span>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ── Right panel tabs ──────────────────────────────────────────────────────────

function PanelTabs() {
  const { rightPanel, setRightPanel } = useStore();
  const tabs: [RightPanel, string][] = [
    ["log",       "Log"],
    ["editor",    "Editor"],
    ["analytics", "Analytics"],
    ["seeds",     "Seeds"],
    ["preview",   "Preview"],
  ];
  return (
    <div style={{ display: "flex", borderBottom: "1px solid var(--border-dim)", flexShrink: 0, overflowX: "auto" }}>
      {tabs.map(([p, l]) => (
        <div
          key={p}
          onClick={() => setRightPanel(p)}
          style={{
            cursor: "pointer", padding: "8px 12px", fontSize: 10,
            fontWeight: 700, letterSpacing: ".06em", whiteSpace: "nowrap", flexShrink: 0,
            borderBottom: `2px solid ${rightPanel === p ? "var(--accent)" : "transparent"}`,
            color: rightPanel === p ? "var(--accent)" : "var(--text-faint)",
            transition: "all .15s",
          }}
        >
          {l}
        </div>
      ))}
    </div>
  );
}

// ── Pipeline log ──────────────────────────────────────────────────────────────

function PipelineLog() {
  const { logs, pipelineState } = useStore();
  const logRef = useRef<HTMLDivElement>(null);

  // Auto-scroll
  React.useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight;
  }, [logs.length]);

  return (
    <div ref={logRef} style={{ flex: 1, overflowY: "auto", padding: 12 }}>
      {logs.length === 0 && (
        <div style={{ fontSize: 11, color: "var(--text-faint)", padding: 8 }}>
          Waiting for pipeline to start…
        </div>
      )}
      {logs.map((log, i) => {
        const a = getAgent(log.agent_id);
        return (
          <div key={i} className="fade-in" style={{ marginBottom: 12 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 3 }}>
              <span style={{
                fontSize: 9, fontWeight: 700, padding: "1px 6px", borderRadius: 2,
                background: `${a.color}14`, color: a.color,
                letterSpacing: ".08em", fontFamily: "'JetBrains Mono', monospace",
              }}>{a.short}</span>
              <span className="mono" style={{ fontSize: 9, color: "var(--text-faint)" }}>
                {new Date(log.timestamp * 1000).toLocaleTimeString()}
              </span>
            </div>
            <div style={{ fontSize: 11, color: "var(--text-muted)", lineHeight: 1.55, paddingLeft: 2 }}>
              {log.message}
            </div>
          </div>
        );
      })}
      {pipelineState?.status === "running" && (
        <div className="pulse" style={{ fontSize: 10, color: "var(--text-faint)", padding: "4px 2px" }}>●</div>
      )}
      {pipelineState?.status === "paused" && (
        <div style={{ fontSize: 10, color: "var(--fixing)", padding: "4px 2px", fontWeight: 600 }}>⏸ Paused</div>
      )}
    </div>
  );
}

// ── Row editor ────────────────────────────────────────────────────────────────

function RowEditor() {
  const { acceptRow, flagRow, regenerateRow, showConfirm, editRow } = useStore();
  const selectedRow = useSelectedRow();

  const [localFields, setLocalFields] = useState<Record<string, string>>({});
  const [dirty, setDirty] = useState(false);

  // Sync local fields when selected row changes OR when a non-dirty SSE update arrives
  const prevRowRef = useRef<DatasetRow | null>(null);

  React.useEffect(() => {
    if (!selectedRow) return;

    const prev = prevRowRef.current;
    prevRowRef.current = selectedRow;

    // If user has unsaved edits and the row was updated via SSE, warn them
    if (dirty && prev && prev.id === selectedRow.id) {
      // Row was updated externally while user was editing
      // Show a warning toast — don't silently discard their work
      useStore.getState().addToast(
        "warning",
        "Row updated externally",
        `Row #${selectedRow.id} was modified by the pipeline while you were editing. Save your changes or refresh.`
      );
      return; // Don't overwrite local edits
    }

    // Fresh selection or clean state — sync local fields
    setLocalFields(
      Object.fromEntries(
        Object.entries(selectedRow.fields).map(([k, v]) => [k, String(v ?? "")])
      )
    );
    setDirty(false);
  }, [selectedRow?.id, selectedRow?.status, selectedRow?.fix_rounds]);

  if (!selectedRow) {
    return (
      <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center" }}>
        <div style={{ fontSize: 12, color: "var(--text-faint)", textAlign: "center", lineHeight: 1.9 }}>
          Select a row<br />to inspect or edit
        </div>
      </div>
    );
  }

  const handleSave = async () => {
    // Separate category (stored as __category__ key) from regular fields
    const { "__category__": newCategory, ...fieldEdits } = localFields;

    // Merge edits back into existing fields (only changed keys)
    const mergedFields = { ...selectedRow.fields };
    for (const [k, v] of Object.entries(fieldEdits)) {
      mergedFields[k] = v;
    }

    await editRow(selectedRow.id, mergedFields);

    // If category changed, send a separate update — backend handles it
    if (newCategory !== undefined && newCategory !== selectedRow.category) {
      // Category is stored on the row directly, not inside fields
      // Pass it as a special key the backend recognises
      await editRow(selectedRow.id, { ...mergedFields, __category__: newCategory });
    }

    setDirty(false);
  };

  const handleRegenerate = () => {
    showConfirm({
      message: "Re-run agent on this row?",
      detail: "The row will be regenerated. Current content will be replaced.",
      confirmLabel: "Re-run",
      onConfirm: () => regenerateRow(selectedRow.id),
    });
  };

  const handleFlag = () => {
    showConfirm({
      message: "Flag this row?",
      detail: "Row will be marked as an error for review.",
      confirmLabel: "Flag",
      danger: true,
      onConfirm: () => flagRow(selectedRow.id),
    });
  };

  return (
    <div style={{ flex: 1, overflowY: "auto", padding: 14, display: "flex", flexDirection: "column", gap: 10 }}>
      {/* Header */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <span className="mono" style={{ fontSize: 10, color: "var(--text-faint)" }}>
          ROW #{selectedRow.id}
        </span>
        <div style={{ display: "flex", alignItems: "center", gap: 5 }}>
          <StatusDot status={selectedRow.status} />
          <span className="mono" style={{ fontSize: 9, color: STATUS_COLOR[selectedRow.status], fontWeight: 700 }}>
            {selectedRow.status.toUpperCase()}
          </span>
        </div>
      </div>

      {/* Category */}
      <div>
        <div style={{ fontSize: 9, fontWeight: 700, letterSpacing: ".12em", color: "var(--text-faint)", marginBottom: 5 }}>
          CATEGORY
        </div>
        <FieldInput
          value={localFields["__category__"] ?? selectedRow.category}
          onChange={(e) => {
            setLocalFields((p) => ({ ...p, "__category__": e.target.value }));
            setDirty(true);
          }}
          style={{ height: 32 }}
        />
      </div>

      {/* Error banner */}
      {selectedRow.errors.length > 0 && (
        <div style={{
          background: "#100808", border: "1px solid #2a1515",
          borderRadius: 4, padding: "9px 11px", fontSize: 11,
          color: "var(--error)", lineHeight: 1.55,
        }}>
          <div style={{ fontWeight: 700, fontSize: 9, letterSpacing: ".1em", marginBottom: 5 }}>
            ⚠ VERIFIER FLAGS
          </div>
          {selectedRow.errors.map((e, i) => (
            <div key={i} style={{ marginBottom: i < selectedRow.errors.length - 1 ? 6 : 0 }}>
              <span style={{ opacity: .7 }}>[{e.error_type}] {e.field}: </span>
              {e.description}
            </div>
          ))}
        </div>
      )}

      {/* Fields */}
      {Object.entries(selectedRow.fields).map(([key, _]) => {
        const val = localFields[key] ?? String(selectedRow.fields[key] ?? "");
        const isLong = val.length > 80 || key.toLowerCase().includes("response");
        return (
          <div key={key}>
            <div style={{ fontSize: 9, fontWeight: 700, letterSpacing: ".12em", color: "var(--text-faint)", marginBottom: 5 }}>
              {key.toUpperCase()}
            </div>
            {isLong ? (
              <FieldTextarea
                rows={5}
                value={val}
                onChange={(e) => { setLocalFields((p) => ({ ...p, [key]: e.target.value })); setDirty(true); }}
              />
            ) : (
              <FieldInput
                value={val}
                onChange={(e) => { setLocalFields((p) => ({ ...p, [key]: e.target.value })); setDirty(true); }}
                style={{ height: 32 }}
              />
            )}
          </div>
        );
      })}

      {/* Actions */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 6, marginTop: 4 }}>
        {dirty && (
          <button
            onClick={handleSave}
            style={{
              gridColumn: "1 / -1",
              padding: "8px 0", borderRadius: 4, fontSize: 11, fontWeight: 700,
              cursor: "pointer", background: "var(--accent)", color: "#080c14",
              border: "none", fontFamily: "'Syne', sans-serif",
            }}
          >
            Save Changes
          </button>
        )}
        {selectedRow.status === "error" && (
          <>
            <button onClick={() => acceptRow(selectedRow.id)} style={actionBtn("var(--ok)", "#081808", "1px solid var(--ok)")}>
              ✓ Accept
            </button>
            <button onClick={handleRegenerate} style={actionBtn("#60c8f0", "var(--bg-raised)", "1px solid var(--border)")}>
              ↺ Regenerate
            </button>
          </>
        )}
        {selectedRow.status === "ok" && (
          <button onClick={handleFlag} style={actionBtn("var(--error)", "#100808", "1px solid #2a1515")}>
            ⚑ Flag
          </button>
        )}
        <button onClick={handleRegenerate} style={actionBtn("#f97316", "#100800", "1px solid #2a1800")}>
          ⟳ Re-run Agent
        </button>
      </div>
    </div>
  );
}

function actionBtn(color: string, bg: string, border: string): React.CSSProperties {
  return {
    padding: "7px 0", borderRadius: 4, fontSize: 11, fontWeight: 600,
    cursor: "pointer", color, background: bg, border,
    fontFamily: "'Syne', sans-serif",
  };
}

// ── Analytics panel ───────────────────────────────────────────────────────────

function AnalyticsPanel() {
  const { rows, currentJobId } = useStore();
  const stats = useRowStats();
  const [tokenData, setTokenData] = React.useState<{
    totals: { prompt: number; completion: number; cached: number; total: number };
    per_agent: Record<string, { prompt: number; completion: number; cached: number }>;
    cache_hit_rate: number;
  } | null>(null);

  // Fetch token usage when panel mounts or job changes
  React.useEffect(() => {
    if (!currentJobId) return;
    import("../lib/api").then(({ getTokenUsage }) =>
      getTokenUsage(currentJobId).then((data) => {
        if (data) setTokenData(data);
      })
    );
    // Refresh every 10s while pipeline is running
    const interval = setInterval(() => {
      import("../lib/api").then(({ getTokenUsage }) =>
        getTokenUsage(currentJobId).then((data) => {
          if (data) setTokenData(data);
        })
      );
    }, 10_000);
    return () => clearInterval(interval);
  }, [currentJobId]);

  const total = rows.length || 1;

  // Category breakdown
  const catMap: Record<string, { total: number; ok: number; error: number }> = {};
  rows.forEach((r) => {
    if (!catMap[r.category]) catMap[r.category] = { total: 0, ok: 0, error: 0 };
    catMap[r.category].total++;
    if (r.status === "ok")    catMap[r.category].ok++;
    if (r.status === "error") catMap[r.category].error++;
  });

  // Error type breakdown
  const errTypes: Record<string, number> = {};
  rows.forEach((r) => r.errors.forEach((e) => {
    errTypes[e.error_type] = (errTypes[e.error_type] ?? 0) + 1;
  }));
  const totalErrors = Object.values(errTypes).reduce((a, b) => a + b, 0) || 1;

  const statusBars = [
    { label: "OK",      count: stats.ok,      color: "var(--ok)" },
    { label: "Errors",  count: stats.error,   color: "var(--error)" },
    { label: "Fixing",  count: stats.fixing,  color: "var(--fixing)" },
    { label: "Pending", count: stats.pending, color: "var(--text-faint)" },
  ];

  return (
    <div style={{ flex: 1, overflowY: "auto", padding: 14 }}>
      <div style={{ fontSize: 10, fontWeight: 700, letterSpacing: ".12em", color: "var(--text-faint)", marginBottom: 14 }}>
        DATASET HEALTH
      </div>

      {/* Stacked bar */}
      <div style={{ background: "var(--bg-surface)", borderRadius: 6, padding: 12, marginBottom: 12, border: "1px solid var(--border)" }}>
        <div style={{ fontSize: 9, fontWeight: 700, letterSpacing: ".1em", color: "var(--text-faint)", marginBottom: 10 }}>
          STATUS BREAKDOWN
        </div>
        <div style={{ display: "flex", height: 8, borderRadius: 4, overflow: "hidden", marginBottom: 12 }}>
          {statusBars.map((b) => (
            <div key={b.label} style={{ flex: b.count || 0.01, background: b.color, transition: "flex .5s ease" }} />
          ))}
        </div>
        {statusBars.map((b) => (
          <div key={b.label} style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 6 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 7 }}>
              <div style={{ width: 8, height: 8, borderRadius: 2, background: b.color }} />
              <span style={{ fontSize: 11, color: "var(--text-muted)" }}>{b.label}</span>
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <div style={{ width: 60, height: 3, background: "var(--border)", borderRadius: 2, overflow: "hidden" }}>
                <div style={{ width: `${(b.count / total) * 100}%`, height: "100%", background: b.color }} />
              </div>
              <span className="mono" style={{ fontSize: 10, color: b.color, minWidth: 24, textAlign: "right" }}>{b.count}</span>
              <span className="mono" style={{ fontSize: 9, color: "var(--text-faint)", minWidth: 32, textAlign: "right" }}>
                {total ? ((b.count / total) * 100).toFixed(0) : 0}%
              </span>
            </div>
          </div>
        ))}
      </div>

      {/* Category breakdown */}
      <div style={{ background: "var(--bg-surface)", borderRadius: 6, padding: 12, marginBottom: 12, border: "1px solid var(--border)" }}>
        <div style={{ fontSize: 9, fontWeight: 700, letterSpacing: ".1em", color: "var(--text-faint)", marginBottom: 10 }}>
          BY CATEGORY
        </div>
        {Object.entries(catMap).map(([cat, d]) => (
          <div key={cat} style={{ marginBottom: 8 }}>
            <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 3 }}>
              <span style={{ fontSize: 10, color: "#60c8f0", fontWeight: 600 }}>{cat}</span>
              <span className="mono" style={{ fontSize: 9, color: "var(--text-faint)" }}>{d.ok}/{d.total}</span>
            </div>
            <div style={{ height: 3, background: "var(--border)", borderRadius: 2, overflow: "hidden" }}>
              <div style={{ display: "flex", height: "100%" }}>
                <div style={{ flex: d.ok, background: "var(--ok)" }} />
                <div style={{ flex: d.error, background: "var(--error)" }} />
                <div style={{ flex: Math.max(0, d.total - d.ok - d.error), background: "var(--text-faint)" }} />
              </div>
            </div>
          </div>
        ))}
        {Object.keys(catMap).length === 0 && (
          <div style={{ fontSize: 11, color: "var(--text-faint)" }}>No rows yet.</div>
        )}
      </div>

      {/* Error types */}
      {Object.keys(errTypes).length > 0 && (
        <div style={{ background: "var(--bg-surface)", borderRadius: 6, padding: 12, border: "1px solid var(--border)", marginBottom: 12 }}>
          <div style={{ fontSize: 9, fontWeight: 700, letterSpacing: ".1em", color: "var(--text-faint)", marginBottom: 10 }}>
            ERROR TYPES
          </div>
          {Object.entries(errTypes).map(([k, v]) => (
            <div key={k} style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 7 }}>
              <span style={{ fontSize: 10, color: "var(--text-muted)", minWidth: 90 }}>{k}</span>
              <div style={{ flex: 1, height: 3, background: "var(--border)", borderRadius: 2, overflow: "hidden" }}>
                <div style={{ width: `${(v / totalErrors) * 100}%`, height: "100%", background: "var(--error)" }} />
              </div>
              <span className="mono" style={{ fontSize: 10, color: "var(--error)", minWidth: 16, textAlign: "right" }}>{v}</span>
            </div>
          ))}
        </div>
      )}

      {/* Token usage */}
      {tokenData && (
        <div style={{ background: "var(--bg-surface)", borderRadius: 6, padding: 12, border: "1px solid var(--border)", marginBottom: 12 }}>
          <div style={{ fontSize: 9, fontWeight: 700, letterSpacing: ".1em", color: "var(--text-faint)", marginBottom: 10 }}>
            TOKEN USAGE
          </div>
          {[
            ["Total tokens",    tokenData.totals.total.toLocaleString(),    "var(--text-muted)"],
            ["Prompt tokens",   tokenData.totals.prompt.toLocaleString(),   "var(--text-muted)"],
            ["Completion",      tokenData.totals.completion.toLocaleString(),"var(--text-muted)"],
            ["Cached tokens",   tokenData.totals.cached.toLocaleString(),   "#a3e635"],
            ["Cache hit rate",  `${(tokenData.cache_hit_rate * 100).toFixed(0)}%`, "#a3e635"],
          ].map(([k, v, c]) => (
            <div key={k} style={{ display: "flex", justifyContent: "space-between", padding: "4px 0", borderBottom: "1px solid var(--bg-raised)", fontSize: 11 }}>
              <span style={{ color: "var(--text-muted)" }}>{k}</span>
              <span className="mono" style={{ color: c as string, fontSize: 11 }}>{v}</span>
            </div>
          ))}
          {tokenData.cache_hit_rate > 0 && (
            <div style={{ fontSize: 10, color: "var(--text-faint)", marginTop: 8, lineHeight: 1.5 }}>
              Cached tokens cost ~10% of normal input price (Anthropic).
            </div>
          )}
          {/* Per-agent breakdown */}
          {Object.keys(tokenData.per_agent).length > 0 && (
            <div style={{ marginTop: 10 }}>
              <div style={{ fontSize: 9, fontWeight: 700, letterSpacing: ".1em", color: "var(--text-faint)", marginBottom: 6 }}>
                PER AGENT
              </div>
              {Object.entries(tokenData.per_agent).map(([agent, usage]) => (
                <div key={agent} style={{ display: "flex", justifyContent: "space-between", padding: "3px 0", fontSize: 10 }}>
                  <span style={{ color: "var(--text-muted)" }}>{agent}</span>
                  <span className="mono" style={{ color: "var(--text-faint)" }}>
                    {(usage.prompt + usage.completion).toLocaleString()}
                    {usage.cached > 0 && <span style={{ color: "#a3e635" }}> ({usage.cached.toLocaleString()} cached)</span>}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ── Seeds panel ───────────────────────────────────────────────────────────────

function SeedsPanel() {
  const { currentJobId } = useStore();
  const [seeds, setSeeds] = useState<Array<{
    category: string;
    fields: Record<string, unknown>;
    is_edge_case: boolean;
    edge_case_type: string | null;
  }>>([]);
  const [blueprint, setBlueprint] = useState<string>("");
  const [loading, setLoading] = useState(false);
  const [activeTab, setActiveTab] = useState<"seeds" | "blueprint">("seeds");

  React.useEffect(() => {
    if (!currentJobId) return;
    setLoading(true);
    import("../lib/api").then(({ getJobSeeds }) =>
      getJobSeeds(currentJobId).then((data) => {
        if (data) {
          setSeeds(data.seeds ?? []);
          setBlueprint(data.blueprint ?? "");
        }
        setLoading(false);
      })
    );
  }, [currentJobId]);

  return (
    <div style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden" }}>
      {/* Sub-tabs */}
      <div style={{ display: "flex", borderBottom: "1px solid var(--border-dim)", flexShrink: 0 }}>
        {(["seeds", "blueprint"] as const).map((t) => (
          <div
            key={t}
            onClick={() => setActiveTab(t)}
            style={{
              cursor: "pointer", padding: "6px 12px", fontSize: 9,
              fontWeight: 700, letterSpacing: ".08em",
              borderBottom: `2px solid ${activeTab === t ? "var(--accent)" : "transparent"}`,
              color: activeTab === t ? "var(--accent)" : "var(--text-faint)",
              transition: "all .15s",
            }}
          >
            {t === "seeds" ? `SEEDS (${seeds.length})` : "BLUEPRINT"}
          </div>
        ))}
      </div>

      {loading && (
        <div style={{ padding: 16, fontSize: 11, color: "var(--text-faint)" }}>
          Loading seeds…
        </div>
      )}

      {/* Seeds list */}
      {!loading && activeTab === "seeds" && (
        <div style={{ flex: 1, overflowY: "auto", padding: 12 }}>
          {seeds.length === 0 ? (
            <div style={{ fontSize: 11, color: "var(--text-faint)", textAlign: "center", marginTop: 32, lineHeight: 1.9 }}>
              Seeds are generated by the Generator agent.<br />
              They appear here once that stage completes.
            </div>
          ) : seeds.map((s, i) => (
            <div key={i} style={{
              marginBottom: 10, background: "var(--bg-surface)",
              border: "1px solid var(--border)", borderRadius: 5, padding: 10,
            }}>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 6 }}>
                <span style={{
                  fontSize: 9, fontWeight: 700, padding: "1px 6px", borderRadius: 2,
                  background: "#60c8f018", color: "#60c8f0",
                  letterSpacing: ".08em", fontFamily: "'JetBrains Mono', monospace",
                }}>{s.category}</span>
                {s.is_edge_case && (
                  <span style={{ fontSize: 9, color: "var(--fixing)", fontStyle: "italic" }}>
                    edge: {s.edge_case_type ?? "unknown"}
                  </span>
                )}
              </div>
              {Object.entries(s.fields).map(([k, v]) => (
                <div key={k} style={{ marginBottom: 2 }}>
                  <span className="mono" style={{ fontSize: 9, color: "var(--text-faint)" }}>{k}: </span>
                  <span className="mono" style={{ fontSize: 10, color: "var(--text-muted)", lineHeight: 1.6 }}>
                    {String(v).slice(0, 200)}{String(v).length > 200 ? "…" : ""}
                  </span>
                </div>
              ))}
            </div>
          ))}
        </div>
      )}

      {/* Blueprint */}
      {!loading && activeTab === "blueprint" && (
        <div style={{ flex: 1, overflowY: "auto", padding: 12 }}>
          {!blueprint ? (
            <div style={{ fontSize: 11, color: "var(--text-faint)" }}>
              Blueprint not yet available.
            </div>
          ) : (
            <pre style={{
              fontSize: 10, color: "var(--text-muted)", lineHeight: 1.7,
              whiteSpace: "pre-wrap", wordBreak: "break-word",
              fontFamily: "'JetBrains Mono', monospace",
            }}>
              {blueprint}
            </pre>
          )}
        </div>
      )}
    </div>
  );
}

// ── Preview panel ─────────────────────────────────────────────────────────────

function PreviewPanel() {
  const { rows } = useStore();
  const [format, setFormat] = useState<"jsonl" | "csv" | "json">("jsonl");

  const okRows = rows.filter((r) => r.status === "ok").slice(0, 20);

  const preview = React.useMemo(() => {
    if (format === "jsonl") {
      return okRows.map((r) => JSON.stringify(r.fields)).join("\n");
    }
    if (format === "json") {
      return JSON.stringify(okRows.map((r) => r.fields), null, 2);
    }
    if (format === "csv") {
      if (okRows.length === 0) return "";
      const headers = Object.keys(okRows[0].fields);
      const headerRow = headers.join(",");
      const dataRows = okRows.map((r) =>
        headers.map((h) => {
          const v = String(r.fields[h] ?? "").replace(/"/g, '""');
          return v.includes(",") || v.includes('"') || v.includes("\n") ? `"${v}"` : v;
        }).join(",")
      );
      return [headerRow, ...dataRows].join("\n");
    }
    return "";
  }, [okRows, format]);

  return (
    <div style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden" }}>
      <div style={{
        padding: "8px 14px", borderBottom: "1px solid var(--border-dim)",
        display: "flex", alignItems: "center", gap: 8, flexShrink: 0,
      }}>
        <span style={{ fontSize: 9, fontWeight: 700, letterSpacing: ".12em", color: "var(--text-faint)" }}>FORMAT</span>
        {(["jsonl", "csv", "json"] as const).map((f) => (
          <button
            key={f}
            onClick={() => setFormat(f)}
            style={{
              padding: "2px 8px", borderRadius: 3, fontSize: 9, fontWeight: 700,
              letterSpacing: ".06em", cursor: "pointer",
              fontFamily: "'Syne', sans-serif",
              background: format === f ? "#f0c04018" : "transparent",
              color: format === f ? "var(--accent)" : "var(--text-faint)",
              border: `1px solid ${format === f ? "var(--accent)" : "var(--border)"}`,
            }}
          >
            {f.toUpperCase()}
          </button>
        ))}
        <div style={{ flex: 1 }} />
        <span className="mono" style={{ fontSize: 9, color: "var(--text-faint)" }}>
          {rows.filter((r) => r.status === "ok").length} verified rows
        </span>
      </div>
      <div style={{ flex: 1, overflowY: "auto", padding: 12 }}>
        {okRows.length === 0 ? (
          <div style={{ fontSize: 11, color: "var(--text-faint)" }}>No verified rows yet.</div>
        ) : (
          <>
            <pre className="mono" style={{
              fontSize: 9, color: "var(--text-muted)", lineHeight: 1.7,
              whiteSpace: "pre-wrap", wordBreak: "break-all",
            }}>
              {preview}
            </pre>
            {rows.filter((r) => r.status === "ok").length > 20 && (
              <div className="mono" style={{ fontSize: 9, color: "var(--text-faint)", marginTop: 8 }}>
                … {rows.filter((r) => r.status === "ok").length - 20} more rows
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

// ── Right panel ───────────────────────────────────────────────────────────────

function RightPanel() {
  const { rightPanel } = useStore();
  return (
    <div style={{
      width: 346, borderLeft: "1px solid var(--border-dim)",
      display: "flex", flexDirection: "column", flexShrink: 0,
      background: "#040810",
    }}>
      <PanelTabs />
      {rightPanel === "log"       && <PipelineLog />}
      {rightPanel === "editor"    && <RowEditor />}
      {rightPanel === "analytics" && <AnalyticsPanel />}
      {rightPanel === "seeds"     && <SeedsPanel />}
      {rightPanel === "preview"   && <PreviewPanel />}
    </div>
  );
}

// ── Header controls ───────────────────────────────────────────────────────────

function WorkspaceHeader() {
  const {
    currentConfig,
    pauseJob, resumeJob, cancelJob,
    showConfirm,
  } = useStore();
  const { pct, verified, total, isRunning, isPaused, status } = usePipelineStatus();
  const paused = isPaused;

  const handleStop = () => showConfirm({
    message: "Stop pipeline?",
    detail:  "Progress is saved. You can resume from History later.",
    confirmLabel: "Stop",
    danger: true,
    onConfirm: cancelJob,
  });

  return (
    <div style={{
      display: "flex", alignItems: "center", gap: 12,
      padding: "0 18px", height: 46,
      borderBottom: "1px solid var(--border-dim)",
      background: "#04070e", flexShrink: 0,
    }}>
      {/* Project name */}
      <div className="mono" style={{
        fontSize: 10, color: "var(--text-faint)",
        maxWidth: 260, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
      }}>
        {currentConfig?.name ?? "No project"}
      </div>

      <div style={{ flex: 1 }} />

      {/* Progress */}
      {total > 0 && (
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <span className="mono" style={{ fontSize: 10, color: "#60c8f0" }}>
            <span className={isRunning ? "pulse" : ""} style={{ display: "inline-block" }}>●</span>
            {" "}{verified}/{total}
          </span>
          <div style={{ width: 72, height: 3, background: "var(--bg-surface)", borderRadius: 2, overflow: "hidden" }}>
            <div style={{
              width: `${pct}%`, height: "100%",
              background: "linear-gradient(90deg, var(--accent), #f97316)",
              transition: "width .4s",
            }} />
          </div>
        </div>
      )}

      {/* Pause / Resume */}
      {(isRunning || paused) && (
        <Btn
          variant="ghost"
          onClick={paused ? resumeJob : pauseJob}
          style={{
            background: paused ? "#0a1f0a" : "#1a1200",
            color: paused ? "var(--ok)" : "var(--accent)",
            border: `1px solid ${paused ? "var(--ok)" : "var(--accent)"}`,
          }}
        >
          {paused ? "▶ Resume" : "⏸ Pause"}
        </Btn>
      )}

      {/* Stop */}
      {(isRunning || paused) && (
        <Btn variant="danger" onClick={handleStop}>■ Stop</Btn>
      )}
    </div>
  );
}

// ── Status bar ────────────────────────────────────────────────────────────────

function StatusBar() {
  const { pipelineState } = useStore();
  const status  = pipelineState?.status ?? "idle";
  const paused  = status === "paused";

  const agStates = pipelineState?.agent_states ?? {};
  const activeModels = Object.entries(agStates)
    .filter(([, s]) => s.model_used)
    .slice(0, 3)
    .map(([id, s]) => `${getAgent(id as AgentID).short}·${s.model_used?.split("/").pop()}`)
    .join("  ");

  return (
    <div style={{
      display: "flex", alignItems: "center", gap: 10,
      padding: "0 16px", height: 22,
      borderTop: "1px solid #090f1c",
      background: "#030608", flexShrink: 0,
      fontSize: 9, color: "var(--text-faint)",
      fontFamily: "'JetBrains Mono', monospace",
    }}>
      {activeModels && <span>{activeModels}</span>}
      <div style={{ flex: 1 }} />
      <span style={{ color: paused ? "var(--fixing)" : "var(--ok)" }}>
        {paused ? "⏸ paused" : status === "running" ? "● pipeline running" : status === "complete" ? "✓ complete" : ""}
      </span>
    </div>
  );
}

// ── Main export ───────────────────────────────────────────────────────────────

export function WorkspaceScreen() {
  return (
    <>
      <WorkspaceHeader />
      <div style={{ display: "flex", flex: 1, overflow: "hidden" }}>
        <AgentSidebar />
        <DatasetTable />
        <RightPanel />
      </div>
      <StatusBar />
    </>
  );
}
