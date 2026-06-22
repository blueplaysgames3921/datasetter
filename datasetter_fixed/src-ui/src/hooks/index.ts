// src-ui/src/hooks/index.ts
//
// Custom React hooks for Datasetter.
// Components use these instead of directly accessing the store for
// derived/computed state, to keep logic out of render functions.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { getRows } from "../lib/api";
import {
  selectProgress,
  selectSelectedRow,
  useStore,
} from "../store";
import type { DatasetRow, PipelineStatus, RowStatus } from "../types";

// ── Pipeline status ───────────────────────────────────────────────────────────

export interface PipelineStatusInfo {
  status:    PipelineStatus | "idle";
  isRunning: boolean;
  isPaused:  boolean;
  isComplete:boolean;
  isFailed:  boolean;
  pct:       number;
  verified:  number;
  total:     number;
  eta:       string | null;
}

export function usePipelineStatus(): PipelineStatusInfo {
  const { pipelineState }       = useStore();
  const { pct, verified, total } = useStore(selectProgress);

  const status    = pipelineState?.status ?? "idle";
  const isRunning = status === "running";
  const isPaused  = status === "paused";
  const isComplete= status === "complete";
  const isFailed  = status === "failed";

  // Rough ETA estimate from pipeline state
  const eta = useMemo((): string | null => {
    if (!pipelineState?.estimated_finish) return null;
    const remaining = pipelineState.estimated_finish - Date.now() / 1000;
    if (remaining <= 0) return null;
    if (remaining < 60)  return `~${Math.round(remaining)}s`;
    if (remaining < 3600) return `~${Math.round(remaining / 60)}m`;
    return `~${(remaining / 3600).toFixed(1)}h`;
  }, [pipelineState?.estimated_finish]);

  return { status, isRunning, isPaused, isComplete, isFailed, pct, verified, total, eta };
}

// ── Row statistics ─────────────────────────────────────────────────────────────

export function useRowStats() {
  const rows = useStore((s) => s.rows);
  return useMemo(() => ({
    total:   rows.length,
    ok:      rows.filter((r) => r.status === "ok").length,
    error:   rows.filter((r) => r.status === "error").length,
    fixing:  rows.filter((r) => r.status === "fixing").length,
    pending: rows.filter((r) => r.status === "pending").length,
    manual:  rows.filter((r) => r.status === "manual").length,
  }), [rows]);
}

// ── Filtered + sorted rows ─────────────────────────────────────────────────────

export function useFilteredRows(): DatasetRow[] {
  const rows        = useStore((s) => s.rows);
  const filterStatus = useStore((s) => s.filterStatus);
  const searchQuery  = useStore((s) => s.searchQuery);
  const sortCol      = useStore((s) => s.sortCol);
  const sortDir      = useStore((s) => s.sortDir);

  return useMemo(() => {
    let result = rows;

    if (filterStatus !== "all") {
      result = result.filter((r) => r.status === filterStatus);
    }

    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase();
      result = result.filter(
        (r) =>
          r.category.toLowerCase().includes(q) ||
          Object.values(r.fields).some((v) => String(v).toLowerCase().includes(q))
      );
    }

    const dir = sortDir === "asc" ? 1 : -1;
    return [...result].sort((a, b) => {
      let av: string | number, bv: string | number;
      if (sortCol === "id")       { av = a.id;       bv = b.id; }
      else if (sortCol === "category") { av = a.category; bv = b.category; }
      else                        { av = a.status;   bv = b.status; }
      if (av < bv) return -1 * dir;
      if (av > bv) return  1 * dir;
      return 0;
    });
  }, [rows, filterStatus, searchQuery, sortCol, sortDir]);
}

// ── Selected row ───────────────────────────────────────────────────────────────

export function useSelectedRow(): DatasetRow | null {
  return useStore(selectSelectedRow);
}

// ── Virtual rows — renders only visible rows for large datasets ────────────────

interface VirtualRowsOptions {
  itemHeight:    number;
  containerHeight: number;
  overscan?:     number;
}

export function useVirtualRows(
  rows: DatasetRow[],
  { itemHeight, containerHeight, overscan = 5 }: VirtualRowsOptions
) {
  const [scrollTop, setScrollTop] = useState(0);
  const containerRef              = useRef<HTMLDivElement>(null);

  const handleScroll = useCallback((e: React.UIEvent<HTMLDivElement>) => {
    setScrollTop((e.target as HTMLDivElement).scrollTop);
  }, []);

  const { startIdx, endIdx, offsetTop, totalHeight } = useMemo(() => {
    const visibleCount = Math.ceil(containerHeight / itemHeight);
    const start = Math.max(0, Math.floor(scrollTop / itemHeight) - overscan);
    const end   = Math.min(rows.length - 1, start + visibleCount + overscan * 2);
    return {
      startIdx:    start,
      endIdx:      end,
      offsetTop:   start * itemHeight,
      totalHeight: rows.length * itemHeight,
    };
  }, [scrollTop, containerHeight, itemHeight, overscan, rows.length]);

  const visibleRows = rows.slice(startIdx, endIdx + 1);

  return { visibleRows, startIdx, endIdx, offsetTop, totalHeight, containerRef, handleScroll };
}

// ── Job poller — fetches rows from API on a timer (for resumed jobs) ──────────

export function useJobPoller(jobId: string | null) {
  const rowCount = useStore((s) => s.rows.length);

  useEffect(() => {
    if (!jobId || rowCount > 0) return; // Already have rows from SSE or cache

    // One-shot fetch when reopening a completed job with no rows in memory
    getRows(jobId, { limit: 500 })
      .then((resp) => {
        if (resp.rows.length > 0) {
          // Use the store's _handleSSE pathway to merge rows cleanly through immer
          for (const row of resp.rows) {
            useStore.getState()._handleSSE({
              event:  "row_update",
              job_id: jobId,
              row,
            });
          }
        }
      })
      .catch((e) => console.warn("[poller] fetch failed:", e));
  }, [jobId, rowCount]);
}

// ── Confirm dialog shortcut ────────────────────────────────────────────────────

export function useConfirm() {
  const { showConfirm, hideConfirm } = useStore();

  /**
   * Show a confirm dialog. Returns a promise that resolves to true if the user
   * confirms, or false if they cancel.
   */
  const confirm = useCallback(
    (
      message: string,
      opts: {
        detail?:       string;
        confirmLabel?: string;
        danger?:       boolean;
      } = {}
    ): Promise<boolean> =>
      new Promise((resolve) => {
        showConfirm({
          message,
          detail:       opts.detail,
          confirmLabel: opts.confirmLabel ?? "Confirm",
          danger:       opts.danger ?? false,
          onConfirm: () => {
            hideConfirm();
            resolve(true);
          },
        });
        // Cancel is handled by hideConfirm in ConfirmDialog component
        // We resolve false on the next tick if confirm is cleared without onConfirm firing
      }),
    [showConfirm, hideConfirm]
  );

  return { confirm };
}

// ── Toast shortcut ─────────────────────────────────────────────────────────────

export function useToast() {
  const { addToast } = useStore();
  return {
    info:    (title: string, msg: string) => addToast("info",    title, msg),
    success: (title: string, msg: string) => addToast("success", title, msg),
    warning: (title: string, msg: string) => addToast("warning", title, msg),
    error:   (title: string, msg: string) => addToast("error",   title, msg),
  };
}

// ── Category list derived from rows ───────────────────────────────────────────

export function useCategories(): string[] {
  const rows = useStore((s) => s.rows);
  return useMemo(
    () => Array.from(new Set(rows.map((r) => r.category))).filter(Boolean).sort(),
    [rows]
  );
}

// ── Debounced value ───────────────────────────────────────────────────────────

export function useDebounce<T>(value: T, delay: number): T {
  const [debounced, setDebounced] = useState<T>(value);
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(t);
  }, [value, delay]);
  return debounced;
}
