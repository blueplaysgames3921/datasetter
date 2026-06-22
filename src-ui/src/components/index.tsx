// src-ui/src/components/index.tsx
// All shared primitive components used across screens.

import React, { useEffect, useRef } from "react";
import type { AgentID, AgentStatus, AppSettings, NotificationLevel, Toast } from "../types";
import { getAgent } from "../lib/agents";
import { useStore } from "../store";

// ── AgentBadge ────────────────────────────────────────────────────────────────

interface AgentBadgeProps {
  id: AgentID;
  size?: "sm" | "md";
}

export function AgentBadge({ id, size = "sm" }: AgentBadgeProps) {
  const a = getAgent(id);
  return (
    <span style={{
      display: "inline-block",
      padding: size === "sm" ? "1px 6px" : "3px 10px",
      borderRadius: 3,
      background: `${a.color}18`,
      color: a.color,
      fontSize: size === "sm" ? 9 : 11,
      fontWeight: 700,
      letterSpacing: ".08em",
      fontFamily: "'JetBrains Mono', monospace",
    }}>
      {a.short}
    </span>
  );
}

// ── Toggle ────────────────────────────────────────────────────────────────────

interface ToggleProps {
  value: boolean;
  onChange: (v: boolean) => void;
  disabled?: boolean;
}

export function Toggle({ value, onChange, disabled = false }: ToggleProps) {
  return (
    <div
      onClick={() => !disabled && onChange(!value)}
      style={{
        width: 38, height: 21, borderRadius: 11,
        cursor: disabled ? "not-allowed" : "pointer",
        position: "relative", flexShrink: 0, opacity: disabled ? 0.4 : 1,
        background: value ? "#f0c04020" : "#0d1929",
        border: `1px solid ${value ? "#f0c040" : "var(--border)"}`,
        transition: "all .2s",
      }}
    >
      <div style={{
        width: 15, height: 15, borderRadius: 8,
        position: "absolute", top: 2,
        left: value ? 19 : 2,
        background: value ? "#f0c040" : "#334155",
        transition: "all .2s",
      }} />
    </div>
  );
}

// ── ConfirmDialog ─────────────────────────────────────────────────────────────

export function ConfirmDialog() {
  const { confirm, hideConfirm } = useStore();
  if (!confirm) return null;

  const { message, detail, confirmLabel = "Confirm", danger = false, onConfirm } = confirm;

  return (
    <div
      onClick={hideConfirm}
      style={{
        position: "fixed", inset: 0, zIndex: 1000,
        background: "rgba(4,7,14,.88)",
        display: "flex", alignItems: "center", justifyContent: "center",
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="slide-up"
        style={{
          background: "var(--bg-raised)",
          border: `1px solid ${danger ? "#3a1515" : "var(--border)"}`,
          borderRadius: 8, padding: 28, maxWidth: 400, width: "90%",
        }}
      >
        <div style={{
          fontSize: 15, fontWeight: 700, marginBottom: 8,
          color: danger ? "var(--error)" : "var(--text)",
        }}>
          {danger ? "⚠ " : ""}{message}
        </div>
        {detail && (
          <div style={{ fontSize: 12, color: "var(--text-muted)", lineHeight: 1.65, marginBottom: 22 }}>
            {detail}
          </div>
        )}
        <div style={{ display: "flex", gap: 10, justifyContent: "flex-end" }}>
          <button onClick={hideConfirm} style={cancelBtnStyle}>Cancel</button>
          <button
            onClick={() => { onConfirm(); hideConfirm(); }}
            style={{
              ...baseBtnStyle,
              background: danger ? "#1a0808" : "#f0c04018",
              color: danger ? "var(--error)" : "var(--accent)",
              border: `1px solid ${danger ? "var(--error)" : "var(--accent)"}`,
            }}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}

const baseBtnStyle: React.CSSProperties = {
  padding: "7px 20px", borderRadius: 5, fontSize: 12, fontWeight: 700,
  cursor: "pointer", fontFamily: "'Syne', sans-serif", border: "none",
  transition: "opacity .15s",
};
const cancelBtnStyle: React.CSSProperties = {
  ...baseBtnStyle, background: "transparent",
  color: "var(--text-muted)", border: "1px solid var(--border)",
};

// ── ToastStack ────────────────────────────────────────────────────────────────

const LEVEL_COLORS: Record<NotificationLevel, string> = {
  info: "#60c8f0", success: "#a3e635", warning: "#f0c040", error: "#f43f5e",
};
const LEVEL_ICONS: Record<NotificationLevel, string> = {
  info: "◎", success: "✓", warning: "⚠", error: "✕",
};

function ToastItem({ toast }: { toast: Toast }) {
  const removeToast = useStore((s) => s.removeToast);
  const c = LEVEL_COLORS[toast.level];

  return (
    <div
      className="slide-up"
      style={{
        background: "var(--bg-raised)",
        border: `1px solid ${c}`,
        borderRadius: 6, padding: "10px 16px",
        maxWidth: 340, boxShadow: "0 10px 40px rgba(0,0,0,.7)",
        display: "flex", gap: 10, alignItems: "flex-start",
        cursor: "pointer",
      }}
      onClick={() => removeToast(toast.id)}
    >
      <span style={{ color: c, fontSize: 14, flexShrink: 0, marginTop: 1 }}>
        {LEVEL_ICONS[toast.level]}
      </span>
      <div>
        {toast.title && (
          <div style={{ fontSize: 12, fontWeight: 700, color: c, marginBottom: 2 }}>
            {toast.title}
          </div>
        )}
        {toast.message && (
          <div style={{ fontSize: 11, color: "var(--text-muted)", lineHeight: 1.5 }}>
            {toast.message}
          </div>
        )}
      </div>
    </div>
  );
}

export function ToastStack() {
  const toasts = useStore((s) => s.toasts);
  return (
    <div style={{
      position: "fixed", top: 16, right: 16, zIndex: 9999,
      display: "flex", flexDirection: "column", gap: 8,
      pointerEvents: "none",
    }}>
      {toasts.map((t) => (
        <div key={t.id} style={{ pointerEvents: "all" }}>
          <ToastItem toast={t} />
        </div>
      ))}
    </div>
  );
}

// ── AgentDetailModal ──────────────────────────────────────────────────────────

export function AgentDetailModal() {
  const { agentDetailId, closeAgentDetail, pipelineState, settings, saveSettings } = useStore();
  if (!agentDetailId) return null;

  const a       = getAgent(agentDetailId);
  const agState = pipelineState?.agent_states?.[agentDetailId];
  const status  = agState?.status ?? "idle";
  const m       = settings?.agent_models?.assignments?.[agentDetailId];

  const updateMode = (mode: string) => {
    if (!settings) return;
    const updated = {
      ...settings,
      agent_models: {
        ...settings.agent_models,
        assignments: {
          ...settings.agent_models?.assignments,
          [agentDetailId]: { ...m, agent_id: agentDetailId, mode },
        },
      },
    };
    saveSettings(updated as AppSettings);
  };

  return (
    <div
      onClick={closeAgentDetail}
      style={{
        position: "fixed", inset: 0, zIndex: 600,
        background: "rgba(4,7,14,.8)",
        display: "flex", alignItems: "center", justifyContent: "center",
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="slide-up"
        style={{
          background: "var(--bg-raised)",
          border: `1px solid ${a.color}`,
          borderRadius: 8, padding: 24, width: 420, maxWidth: "92vw",
        }}
      >
        {/* Header */}
        <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 14 }}>
          <div style={{
            width: 42, height: 42, borderRadius: 6, flexShrink: 0,
            background: `${a.color}14`, border: `1px solid ${a.color}`,
            display: "flex", alignItems: "center", justifyContent: "center",
            fontSize: 12, fontWeight: 700, color: a.color,
            fontFamily: "'JetBrains Mono', monospace",
          }}>
            {a.short}
          </div>
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: 15, fontWeight: 700 }}>{a.label}</div>
            <div style={{
              fontSize: 9, fontWeight: 700, letterSpacing: ".08em",
              color: status === "running" ? a.color : "var(--text-faint)",
            }} className={status === "running" ? "pulse" : ""}>
              {status === "running" ? "● RUNNING"
                : status === "done" ? "✓ DONE"
                  : status === "failed" ? "✕ FAILED"
                    : "IDLE"}
            </div>
          </div>
          <button
            onClick={closeAgentDetail}
            style={{ background: "none", border: "none", color: "var(--text-dim)", cursor: "pointer", fontSize: 20 }}
          >×</button>
        </div>

        <div style={{ fontSize: 12, color: "var(--text-muted)", lineHeight: 1.65, marginBottom: 18 }}>
          {a.desc}
        </div>

        {agState?.model_used && (
          <div style={{ fontSize: 10, color: "var(--text-dim)", marginBottom: 14 }}>
            <span style={{ color: "var(--text-faint)", marginRight: 6 }}>MODEL</span>
            <span className="mono" style={{ color: "var(--text-muted)" }}>{agState.model_used}</span>
          </div>
        )}

        {agState?.current_task && (
          <div style={{ fontSize: 10, color: "var(--text-dim)", marginBottom: 14 }}>
            <span style={{ color: "var(--text-faint)", marginRight: 6 }}>TASK</span>
            <span style={{ color: "var(--text-muted)" }}>{agState.current_task}</span>
          </div>
        )}

        {/* Model assignment */}
        <div style={{ fontSize: 9, fontWeight: 700, letterSpacing: ".12em", color: "var(--text-faint)", marginBottom: 10 }}>
          MODEL ASSIGNMENT
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10, marginBottom: 14 }}>
          {[["CLOUD", m?.cloud_model ?? "—"], ["LOCAL", m?.local_model ?? "—"]].map(([lbl, val]) => (
            <div key={lbl}>
              <div style={{ fontSize: 9, color: "var(--text-faint)", fontWeight: 700, letterSpacing: ".1em", marginBottom: 5 }}>{lbl}</div>
              <input style={{
                width: "100%", background: "var(--bg-surface)",
                border: "1px solid var(--border)", borderRadius: 4,
                color: val === "—" ? "var(--text-faint)" : "var(--text)",
                padding: "6px 10px", height: 32,
                fontFamily: "'JetBrains Mono', monospace", fontSize: 11,
                outline: "none", opacity: val === "—" ? 0.5 : 1,
              }} defaultValue={val} readOnly />
            </div>
          ))}
        </div>

        <div style={{ fontSize: 9, fontWeight: 700, letterSpacing: ".12em", color: "var(--text-faint)", marginBottom: 8 }}>
          INFERENCE MODE
        </div>
        <div style={{ display: "flex", gap: 6 }}>
          {["cloud", "local", "auto"].map((mode) => {
            const cloudOnly = !m?.local_model;
            const localOnly = !m?.cloud_model;
            const disabled  = (mode === "cloud" && cloudOnly && m?.cloud_model === null)
                           || (mode === "local" && localOnly && m?.local_model === null);
            const active    = (m?.mode ?? "auto") === mode;
            return (
              <button
                key={mode}
                onClick={() => updateMode(mode)}
                disabled={disabled}
                style={{
                  flex: 1, padding: "7px 0", borderRadius: 4,
                  fontSize: 11, fontWeight: 600,
                  cursor: disabled ? "not-allowed" : "pointer",
                  opacity: disabled ? 0.3 : 1,
                  background: active ? `${a.color}18` : "transparent",
                  color: active ? a.color : "var(--text-faint)",
                  border: `1px solid ${active ? a.color : "var(--border)"}`,
                  fontFamily: "'Syne', sans-serif", transition: "all .15s",
                }}
              >
                {mode[0].toUpperCase() + mode.slice(1)}
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}

// ── Shared input styles ───────────────────────────────────────────────────────

export const inputStyle: React.CSSProperties = {
  background: "var(--bg-surface)",
  border: "1px solid var(--border)",
  borderRadius: 4,
  color: "var(--text)",
  padding: "8px 10px",
  fontFamily: "'JetBrains Mono', monospace",
  fontSize: 11,
  width: "100%",
  outline: "none",
  lineHeight: 1.5,
  transition: "border .15s",
};

export const textareaStyle: React.CSSProperties = {
  ...inputStyle,
  resize: "vertical",
};

// ── FieldInput (focus border) ─────────────────────────────────────────────────

interface FieldInputProps extends React.InputHTMLAttributes<HTMLInputElement> {
  style?: React.CSSProperties;
}

export function FieldInput({ style: extraStyle, ...props }: FieldInputProps) {
  const ref = useRef<HTMLInputElement>(null);
  return (
    <input
      ref={ref}
      {...props}
      style={{ ...inputStyle, ...extraStyle }}
      onFocus={(e) => { e.target.style.borderColor = "var(--accent)"; props.onFocus?.(e); }}
      onBlur={(e) => { e.target.style.borderColor = "var(--border)"; props.onBlur?.(e); }}
    />
  );
}

interface FieldTextareaProps extends React.TextareaHTMLAttributes<HTMLTextAreaElement> {
  style?: React.CSSProperties;
}

export function FieldTextarea({ style: extraStyle, ...props }: FieldTextareaProps) {
  return (
    <textarea
      {...props}
      style={{ ...textareaStyle, ...extraStyle }}
      onFocus={(e) => { e.target.style.borderColor = "var(--accent)"; props.onFocus?.(e); }}
      onBlur={(e) => { e.target.style.borderColor = "var(--border)"; props.onBlur?.(e); }}
    />
  );
}

// ── Btn ───────────────────────────────────────────────────────────────────────

interface BtnProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: "primary" | "ghost" | "danger" | "success" | "info";
  size?: "sm" | "md";
}

export function Btn({ variant = "ghost", size = "sm", style, children, ...props }: BtnProps) {
  const variantStyles: Record<string, React.CSSProperties> = {
    primary: { background: "var(--accent)", color: "#080c14", border: "none" },
    ghost:   { background: "transparent", color: "var(--text-muted)", border: "1px solid var(--border)" },
    danger:  { background: "#1a0808", color: "var(--error)", border: "1px solid var(--error)" },
    success: { background: "#081808", color: "var(--ok)", border: "1px solid var(--ok)" },
    info:    { background: "var(--bg-raised)", color: "#60c8f0", border: "1px solid var(--border)" },
  };
  const sizeStyles: Record<string, React.CSSProperties> = {
    sm: { padding: "4px 12px", fontSize: 11 },
    md: { padding: "9px 24px", fontSize: 13 },
  };
  return (
    <button
      {...props}
      style={{
        borderRadius: 4, fontWeight: 700, cursor: "pointer",
        fontFamily: "'Syne', sans-serif", transition: "opacity .15s",
        letterSpacing: ".04em",
        ...variantStyles[variant],
        ...sizeStyles[size],
        ...style,
        ...(props.disabled ? { opacity: 0.4, cursor: "not-allowed" } : {}),
      }}
    >
      {children}
    </button>
  );
}

// ── SectionLabel ──────────────────────────────────────────────────────────────

export function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <div style={{
      fontSize: 9, fontWeight: 700, letterSpacing: ".15em",
      color: "var(--text-faint)", marginBottom: 8,
    }}>
      {children}
    </div>
  );
}

// ── ConfigBlock / ConfigRow ───────────────────────────────────────────────────

export function ConfigBlock({ children }: { children: React.ReactNode }) {
  return (
    <div style={{
      background: "var(--bg-surface)", border: "1px solid var(--border)",
      borderRadius: 6, overflow: "hidden", marginBottom: 22,
    }}>
      {children}
    </div>
  );
}

export function ConfigRow({
  label, children,
}: { label: React.ReactNode; children: React.ReactNode }) {
  return (
    <div style={{
      display: "flex", alignItems: "center", justifyContent: "space-between",
      padding: "10px 14px", borderBottom: "1px solid var(--bg-base)",
    }}>
      <span style={{ fontSize: 12, color: "var(--text-muted)" }}>{label}</span>
      {children}
    </div>
  );
}

// ── StatusDot ─────────────────────────────────────────────────────────────────

const STATUS_COLORS: Record<string, string> = {
  ok: "var(--ok)", error: "var(--error)",
  fixing: "var(--fixing)", pending: "var(--pending)", manual: "#a78bfa",
};

export function StatusDot({ status, pulse = false }: { status: string; pulse?: boolean }) {
  return (
    <div style={{
      width: 6, height: 6, borderRadius: 3, flexShrink: 0,
      background: STATUS_COLORS[status] ?? "var(--text-dim)",
      ...(pulse ? { animation: "pulse 1.2s ease-in-out infinite" } : {}),
    }} />
  );
}

// ── SimpleSelect ──────────────────────────────────────────────────────────────

interface SimpleSelectProps {
  value: string;
  onChange: (v: string) => void;
  options: string[] | Array<{ value: string; label: string }>;
  style?: React.CSSProperties;
}

export function SimpleSelect({ value, onChange, options, style }: SimpleSelectProps) {
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      style={{
        background: "var(--bg-surface)", border: "1px solid var(--border)",
        borderRadius: 4, color: "var(--text)", padding: "4px 8px",
        fontFamily: "'JetBrains Mono', monospace", fontSize: 11,
        outline: "none", cursor: "pointer", ...style,
      }}
    >
      {options.map((o) =>
        typeof o === "string"
          ? <option key={o} value={o}>{o}</option>
          : <option key={o.value} value={o.value}>{o.label}</option>
      )}
    </select>
  );
}
