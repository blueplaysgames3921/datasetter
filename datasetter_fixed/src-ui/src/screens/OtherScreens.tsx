// src-ui/src/screens/OtherScreens.tsx
// HomeScreen, HistoryScreen, SettingsScreen

import React, { useEffect, useState } from "react";
import {
  AgentBadge, Btn, ConfigBlock, ConfigRow,
  FieldInput, SectionLabel, Toggle,
} from "../components";
import { AGENT_META } from "../lib/agents";
import { useStore } from "../store";
import type { AgentID, AppSettings, InferenceMode, SettingsTab } from "../types";

// ─────────────────────────────────────────────────────────────────────────────
// HOME SCREEN
// ─────────────────────────────────────────────────────────────────────────────

export function HomeScreen() {
  const { setScreen, loadProjects, projects } = useStore();

  useEffect(() => { loadProjects(); }, []);

  return (
    <div style={{
      flex: 1, display: "flex", flexDirection: "column",
      alignItems: "center", justifyContent: "center",
      gap: 28, padding: 40, textAlign: "center",
    }}>
      {/* Wordmark */}
      <div>
        <div style={{
          fontSize: 52, fontWeight: 800, letterSpacing: "-.02em",
          color: "var(--accent)", marginBottom: 8,
        }}>
          DATASETTER
        </div>
        <div style={{ fontSize: 14, color: "var(--text-faint)", letterSpacing: ".05em" }}>
          AI-augmented dataset generation &amp; verification
        </div>
      </div>

      {/* Actions */}
      <div style={{ display: "flex", gap: 12 }}>
        <Btn variant="primary" size="md" onClick={() => setScreen("new")}>
          ▶ New Dataset
        </Btn>
        <Btn variant="ghost" size="md" onClick={() => setScreen("history")}>
          Open Recent
        </Btn>
      </div>

      {/* Feature cards */}
      <div style={{
        display: "grid", gridTemplateColumns: "repeat(3, 1fr)",
        gap: 10, maxWidth: 620, width: "100%", textAlign: "left",
      }}>
        {[
          [
            "8-agent pipeline",
            "Orchestrator, Interpreter, Analyser, Researcher, Generator, Scripter, Verifier, Fixer — each with a dedicated role.",
          ],
          [
            "Local + Cloud hybrid",
            "Run heavy models locally, lightweight ones via API. Hardware-aware, auto-configured on first launch.",
          ],
          [
            "Full human control",
            "Edit any row, flag errors, re-run any agent, pause anytime. Nothing is a black box.",
          ],
        ].map(([t, d]) => (
          <div key={t} style={{
            padding: 16, borderRadius: 6,
            background: "var(--bg-surface)", border: "1px solid var(--border)",
          }}>
            <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 6, color: "var(--text-muted)" }}>{t}</div>
            <div style={{ fontSize: 11, color: "var(--text-faint)", lineHeight: 1.55 }}>{d}</div>
          </div>
        ))}
      </div>

      {/* Recent projects (up to 3) */}
      {projects.length > 0 && (
        <div style={{ maxWidth: 480, width: "100%", textAlign: "left" }}>
          <div style={{ fontSize: 9, fontWeight: 700, letterSpacing: ".15em", color: "var(--text-faint)", marginBottom: 10 }}>
            RECENT
          </div>
          {projects.slice(0, 3).map((p) => (
            <div
              key={p.job_id}
              onClick={() => useStore.getState().openJob(p.job_id)}
              style={{
                display: "flex", alignItems: "center", justifyContent: "space-between",
                padding: "10px 14px", marginBottom: 6, borderRadius: 5,
                background: "var(--bg-surface)", border: "1px solid var(--border)",
                cursor: "pointer", transition: "border-color .15s",
              }}
              onMouseEnter={(e) => (e.currentTarget.style.borderColor = "var(--text-dim)")}
              onMouseLeave={(e) => (e.currentTarget.style.borderColor = "var(--border)")}
            >
              <div>
                <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 2 }}>{p.name}</div>
                <div className="mono" style={{ fontSize: 10, color: "var(--text-faint)" }}>
                  {p.total_rows.toLocaleString()} rows · {p.output_format.toUpperCase()}
                </div>
              </div>
              <div style={{
                fontSize: 9, fontWeight: 700, padding: "2px 8px", borderRadius: 3,
                background: p.status === "complete" ? "#a3e63514" : "#f0c04014",
                color: p.status === "complete" ? "var(--ok)" : "var(--accent)",
                border: `1px solid ${p.status === "complete" ? "var(--ok)" : "var(--accent)"}`,
              }}>
                {p.status === "complete" ? "DONE" : p.status.toUpperCase()}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// HISTORY SCREEN
// ─────────────────────────────────────────────────────────────────────────────

export function HistoryScreen() {
  const { projects, projectsLoaded, loadProjects, deleteProject, openJob, showConfirm } = useStore();

  useEffect(() => { if (!projectsLoaded) loadProjects(); }, []);

  const handleDelete = (jobId: string, name: string) => {
    showConfirm({
      message: `Delete "${name}"?`,
      detail: "This cannot be undone. All rows, seeds, and exports will be removed.",
      confirmLabel: "Delete",
      danger: true,
      onConfirm: () => deleteProject(jobId),
    });
  };

  return (
    <div style={{ flex: 1, overflowY: "auto", padding: 28 }}>
      <div style={{ fontSize: 18, fontWeight: 800, marginBottom: 4 }}>Project History</div>
      <div style={{ fontSize: 12, color: "var(--text-faint)", marginBottom: 24 }}>
        All datasets saved on this device.
      </div>

      <div style={{ maxWidth: 720 }}>
        {!projectsLoaded && (
          <div style={{ fontSize: 12, color: "var(--text-faint)" }}>Loading…</div>
        )}
        {projectsLoaded && projects.length === 0 && (
          <div style={{ fontSize: 12, color: "var(--text-faint)" }}>
            No projects yet. Start one with New Dataset.
          </div>
        )}
        {projects.map((p) => (
          <div
            key={p.job_id}
            style={{
              padding: 16, marginBottom: 8, borderRadius: 6,
              background: "var(--bg-surface)", border: "1px solid var(--border)",
              display: "flex", alignItems: "center", gap: 14,
              transition: "border-color .15s",
            }}
            onMouseEnter={(e) => (e.currentTarget.style.borderColor = "var(--text-dim)")}
            onMouseLeave={(e) => (e.currentTarget.style.borderColor = "var(--border)")}
          >
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 4 }}>{p.name}</div>
              <div className="mono" style={{ fontSize: 10, color: "var(--text-faint)" }}>
                {p.total_rows.toLocaleString()} rows ·{" "}
                {p.output_format.toUpperCase()} ·{" "}
                {p.pipeline_mode} ·{" "}
                {new Date(p.updated_at * 1000).toLocaleDateString()}
                {p.error_rows > 0 && (
                  <span style={{ color: "var(--error)", marginLeft: 8 }}>
                    ⚠ {p.error_rows} unresolved
                  </span>
                )}
              </div>
            </div>

            <div style={{
              fontSize: 9, fontWeight: 700, padding: "3px 9px", borderRadius: 3,
              letterSpacing: ".09em",
              background: p.status === "running" ? "#f0c04012" : "#a3e63512",
              color: p.status === "running" ? "var(--accent)" : "var(--ok)",
              border: `1px solid ${p.status === "running" ? "var(--accent)" : "var(--ok)"}`,
            }}>
              {p.status === "running" ? "● RUNNING" : "✓ DONE"}
            </div>

            <Btn variant="info" onClick={() => openJob(p.job_id)}>Open</Btn>
            <Btn variant="danger" onClick={() => handleDelete(p.job_id, p.name)}>✕</Btn>
          </div>
        ))}
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────
// SETTINGS SCREEN
// ─────────────────────────────────────────────────────────────────────────────

function SettingsSidebar() {
  const { settingsTab, setSettingsTab } = useStore();
  const tabs: SettingsTab[] = ["Pipeline", "Models", "Verification", "Generation", "APIs", "System"];
  return (
    <div style={{
      width: 154, borderRight: "1px solid var(--border-dim)",
      background: "#040810", flexShrink: 0, paddingTop: 10,
    }}>
      {tabs.map((t) => (
        <button
          key={t}
          onClick={() => setSettingsTab(t)}
          style={{
            display: "block", width: "100%", padding: "10px 18px",
            fontSize: 12, fontWeight: 600, cursor: "pointer",
            textAlign: "left", background: settingsTab === t ? "var(--bg-surface)" : "transparent",
            border: "none", borderLeft: `2px solid ${settingsTab === t ? "var(--accent)" : "transparent"}`,
            color: settingsTab === t ? "var(--accent)" : "var(--text-faint)",
            fontFamily: "'Syne', sans-serif", transition: "all .15s",
          }}
        >
          {t}
        </button>
      ))}
    </div>
  );
}

const SMALL_INPUT_STYLE: React.CSSProperties = {
  background: "var(--bg-base)", border: "1px solid var(--border)",
  borderRadius: 4, color: "var(--text)", padding: "5px 8px",
  fontFamily: "'JetBrains Mono', monospace", fontSize: 11,
  outline: "none", width: 140, textAlign: "right",
};

function PipelineSettings() {
  const { settings, saveSettings } = useStore();
  if (!settings) return null;
  const set = (k: keyof AppSettings) => (v: unknown) =>
    saveSettings({ ...settings, [k]: v });

  return (
    <div style={{ maxWidth: 540 }}>
      <div style={{ fontSize: 18, fontWeight: 800, marginBottom: 4 }}>Pipeline Settings</div>
      <div style={{ fontSize: 12, color: "var(--text-faint)", marginBottom: 22, lineHeight: 1.5 }}>
        Agent activation, retry behavior, and flow control.
      </div>
      <ConfigBlock>
        {([
          ["Max retries per agent",      "default_max_retries",   "number"],
          ["Retry delay (seconds)",       "default_retry_delay",   "number"],
        ] as const).map(([label, key]) => (
          <ConfigRow key={key} label={label}>
            <input
              style={SMALL_INPUT_STYLE}
              type="number"
              defaultValue={settings[key] as number}
              onBlur={(e) => set(key)(parseFloat(e.target.value))}
            />
          </ConfigRow>
        ))}
        <ConfigRow label="Auto-resume on failure">
          <Toggle value={true} onChange={() => {}} />
        </ConfigRow>
        <ConfigRow label="Confirmation on destructive actions">
          <Toggle value={true} onChange={() => {}} />
        </ConfigRow>
      </ConfigBlock>

      <SectionLabel>AGENT ACTIVATION</SectionLabel>
      <ConfigBlock>
        {AGENT_META.map((a) => (
          <ConfigRow key={a.id} label={
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <AgentBadge id={a.id} />
              <span>{a.label}</span>
            </div>
          }>
            <Toggle
              value={!["analyser","researcher"].includes(a.id)}
              onChange={() => {}}
            />
          </ConfigRow>
        ))}
      </ConfigBlock>
    </div>
  );
}

function ModelsSettings() {
  const { settings, saveSettings } = useStore();
  if (!settings) return null;

  const updateMode = (agentId: AgentID, mode: InferenceMode) => {
    const updated: AppSettings = {
      ...settings,
      agent_models: {
        ...settings.agent_models,
        assignments: {
          ...settings.agent_models?.assignments,
          [agentId]: {
            ...(settings.agent_models?.assignments?.[agentId] ?? {}),
            agent_id: agentId,
            mode,
          },
        },
      },
    };
    saveSettings(updated);
  };

  return (
    <div style={{ maxWidth: 640 }}>
      <div style={{ fontSize: 18, fontWeight: 800, marginBottom: 4 }}>Model Configuration</div>
      <div style={{ fontSize: 12, color: "var(--text-faint)", marginBottom: 22, lineHeight: 1.5 }}>
        Assign cloud and local models per agent. Overridable per-job at launch.
      </div>
      {AGENT_META.map((a) => {
        const m    = settings.agent_models?.assignments?.[a.id];
        const mode = m?.mode ?? "auto";
        return (
          <div key={a.id} style={{
            marginBottom: 12, padding: 14, borderRadius: 6,
            background: "var(--bg-surface)", border: "1px solid var(--border)",
          }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12 }}>
              <AgentBadge id={a.id} size="md" />
              <span style={{ fontSize: 13, fontWeight: 600 }}>{a.label}</span>
              <div style={{ marginLeft: "auto", display: "flex", gap: 5 }}>
                {(["cloud","local","auto"] as InferenceMode[]).map((md) => (
                  <button
                    key={md}
                    onClick={() => updateMode(a.id, md)}
                    style={{
                      padding: "3px 10px", borderRadius: 3, fontSize: 10, fontWeight: 600,
                      cursor: "pointer",
                      background: mode === md ? `${a.color}18` : "transparent",
                      color: mode === md ? a.color : "var(--text-faint)",
                      border: `1px solid ${mode === md ? a.color : "var(--border)"}`,
                      fontFamily: "'Syne', sans-serif",
                    }}
                  >
                    {md[0].toUpperCase() + md.slice(1)}
                  </button>
                ))}
              </div>
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10 }}>
              {([["CLOUD", m?.cloud_model ?? "—"], ["LOCAL", m?.local_model ?? "—"]] as const).map(([lbl, val]) => (
                <div key={lbl}>
                  <div style={{ fontSize: 9, color: "var(--text-faint)", fontWeight: 700, letterSpacing: ".1em", marginBottom: 5 }}>{lbl}</div>
                  <input
                    style={{ ...SMALL_INPUT_STYLE, width: "100%", textAlign: "left", opacity: val === "—" ? 0.4 : 1 }}
                    defaultValue={val}
                    readOnly={val === "—"}
                  />
                </div>
              ))}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function VerificationSettings() {
  const { settings, saveSettings } = useStore();
  if (!settings) return null;
  const set = (k: keyof AppSettings) => (v: unknown) => saveSettings({ ...settings, [k]: v });

  return (
    <div style={{ maxWidth: 540 }}>
      <div style={{ fontSize: 18, fontWeight: 800, marginBottom: 4 }}>Verification Settings</div>
      <div style={{ fontSize: 12, color: "var(--text-faint)", marginBottom: 22 }}>
        Default behavior for the Verifier across all jobs.
      </div>
      <ConfigBlock>
        <ConfigRow label="Batch size">
          <input style={SMALL_INPUT_STYLE} type="number" defaultValue={settings.default_batch_size} onBlur={(e) => set("default_batch_size")(parseInt(e.target.value))} />
        </ConfigRow>
        <ConfigRow label={`Strictness (${settings.default_strictness}/5)`}>
          <input type="range" min={1} max={5} value={settings.default_strictness} onChange={(e) => set("default_strictness")(parseInt(e.target.value))} style={{ width: 100 }} />
        </ConfigRow>
        <ConfigRow label="Auto-fix minor errors">
          <Toggle value={settings.default_auto_fix} onChange={(v) => set("default_auto_fix")(v)} />
        </ConfigRow>
        <ConfigRow label="Max fix rounds">
          <input style={SMALL_INPUT_STYLE} type="number" defaultValue={settings.default_max_fix_rounds} onBlur={(e) => set("default_max_fix_rounds")(parseInt(e.target.value))} />
        </ConfigRow>
        <ConfigRow label="KV cache clear interval (rows)">
          <input style={SMALL_INPUT_STYLE} type="number" defaultValue={settings.default_kv_cache_clear_interval} onBlur={(e) => set("default_kv_cache_clear_interval")(parseInt(e.target.value))} />
        </ConfigRow>
      </ConfigBlock>

      <SectionLabel>CHECK TYPES</SectionLabel>
      <ConfigBlock>
        {["Semantic coherence","Logic consistency","Constraint compliance","Cross-row consistency","Format validation","Length constraints"].map((c) => (
          <ConfigRow key={c} label={c}>
            <Toggle value={true} onChange={() => {}} />
          </ConfigRow>
        ))}
      </ConfigBlock>
    </div>
  );
}

function GenerationSettings() {
  const { settings, saveSettings } = useStore();
  if (!settings) return null;
  const set = (k: keyof AppSettings) => (v: unknown) => saveSettings({ ...settings, [k]: v });

  return (
    <div style={{ maxWidth: 540 }}>
      <div style={{ fontSize: 18, fontWeight: 800, marginBottom: 4 }}>Generation Defaults</div>
      <div style={{ fontSize: 12, color: "var(--text-faint)", marginBottom: 22 }}>
        Applied to all new jobs unless overridden at launch.
      </div>
      <ConfigBlock>
        <ConfigRow label="Default rows">
          <input style={SMALL_INPUT_STYLE} type="number" defaultValue={settings.default_rows} onBlur={(e) => set("default_rows")(parseInt(e.target.value))} />
        </ConfigRow>
        <ConfigRow label="Max seed examples">
          <input style={SMALL_INPUT_STYLE} type="number" defaultValue={settings.default_seed_count} onBlur={(e) => set("default_seed_count")(parseInt(e.target.value))} />
        </ConfigRow>
        <ConfigRow label="Default language">
          <input style={SMALL_INPUT_STYLE} defaultValue={settings.default_language} onBlur={(e) => set("default_language")(e.target.value)} />
        </ConfigRow>
        <ConfigRow label={`Diversity (${settings.default_diversity}/5)`}>
          <input type="range" min={1} max={5} value={settings.default_diversity} onChange={(e) => set("default_diversity")(parseInt(e.target.value))} style={{ width: 100 }} />
        </ConfigRow>
      </ConfigBlock>
    </div>
  );
}

function APISettings() {
  const { settings, saveSettings } = useStore();
  if (!settings) return null;

  const setKey = (k: keyof typeof settings.api_keys) => (v: string) =>
    saveSettings({ ...settings, api_keys: { ...settings.api_keys, [k]: v || null } });

  const providers: [string, keyof typeof settings.api_keys, string][] = [
    ["ANTHROPIC",    "anthropic",   "Required — Orchestrator, Scripter default"],
    ["GOOGLE AI",    "google",      "Required — Researcher agent (Gemini)"],
    ["DEEPINFRA",    "deepinfra",   "High diversity — recommended"],
    ["FEATHERLESS",  "featherless", "High diversity — recommended"],
    ["GROQ",         "groq",        "Speed-optimized"],
    ["SAMBANOVA",    "sambanova",   "Speed-optimized"],
    ["FIREWORKS",    "fireworks",   ""],
    ["NOVITA",       "novita",      ""],
    ["SILICONFLOW",  "siliconflow", ""],
    ["RUNPOD",       "runpod",      "Self-hosted cloud"],
  ];

  return (
    <div style={{ maxWidth: 540 }}>
      <div style={{ fontSize: 18, fontWeight: 800, marginBottom: 4 }}>API Keys & Endpoints</div>
      <div style={{ fontSize: 12, color: "var(--text-faint)", marginBottom: 22 }}>
        Stored locally on your device. Never transmitted externally.
      </div>

      {providers.map(([name, key, hint]) => (
        <div key={name} style={{ marginBottom: 14 }}>
          <div style={{ fontSize: 9, fontWeight: 700, letterSpacing: ".1em", color: "var(--text-faint)", marginBottom: 2 }}>{name}</div>
          {hint && <div style={{ fontSize: 10, color: "var(--text-faint)", marginBottom: 5 }}>{hint}</div>}
          <FieldInput
            type="password"
            placeholder="API key"
            defaultValue={settings.api_keys[key] ?? ""}
            onBlur={(e) => setKey(key)(e.target.value)}
          />
        </div>
      ))}

      <div style={{ marginTop: 24, paddingTop: 20, borderTop: "1px solid var(--border)" }}>
        <div style={{ fontSize: 14, fontWeight: 700, marginBottom: 4 }}>Personal Endpoints</div>
        <div style={{ fontSize: 11, color: "var(--text-faint)", marginBottom: 14 }}>
          Any OpenAI-compatible server — Ollama, vLLM, LM Studio, TabbyAPI…
        </div>
        {settings.api_keys.custom_endpoints.map((ep, i) => (
          <div key={i} style={{ padding: 12, borderRadius: 5, background: "var(--bg-surface)", border: "1px solid var(--border)", marginBottom: 10 }}>
            <FieldInput defaultValue={ep.url} placeholder="http://192.168.x.x:11434/v1" style={{ marginBottom: 8 }} />
            <FieldInput defaultValue={ep.label} placeholder="Label (e.g. Home Desktop)" style={{ marginBottom: 6 }} />
            <FieldInput type="password" defaultValue={ep.api_key} placeholder="API key (optional)" />
          </div>
        ))}
        <Btn variant="info" onClick={() => saveSettings({
          ...settings,
          api_keys: {
            ...settings.api_keys,
            custom_endpoints: [...settings.api_keys.custom_endpoints, { url: "", label: "", api_key: "" }],
          },
        })}>
          + Add Endpoint
        </Btn>
      </div>
    </div>
  );
}

function SystemSettings() {
  const { settings, saveSettings, rescanHardware, addToast } = useStore();
  const [reconfiguring, setReconfiguring] = useState(false);
  if (!settings) return null;
  const set = (k: keyof AppSettings) => (v: unknown) => saveSettings({ ...settings, [k]: v });
  const hw = settings.hardware;

  const handleReconfigure = async () => {
    setReconfiguring(true);
    try {
      const { reconfigureModels } = await import("../lib/api");
      await reconfigureModels();
      await useStore.getState().loadSettings();
      addToast("success", "Models reconfigured", "Optimal model assignments updated for your hardware.");
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      addToast("error", "Reconfigure failed", msg);
    } finally {
      setReconfiguring(false);
    }
  };

  return (
    <div style={{ maxWidth: 540 }}>
      <div style={{ fontSize: 18, fontWeight: 800, marginBottom: 4 }}>System</div>
      <div style={{ fontSize: 12, color: "var(--text-faint)", marginBottom: 22 }}>
        Hardware profile, storage, and app behavior.
      </div>

      {/* Hardware */}
      <div style={{ padding: 16, borderRadius: 6, background: "var(--bg-surface)", border: "1px solid var(--border)", marginBottom: 22 }}>
        <div style={{ fontSize: 9, fontWeight: 700, letterSpacing: ".12em", color: "var(--text-faint)", marginBottom: 12 }}>
          HARDWARE PROFILE
        </div>
        {hw ? (
          <>
            {([
              ["GPU",  hw.gpu ? `${hw.gpu.name} — ${hw.gpu.vram_gb} GB VRAM` : "None detected"],
              ["RAM",  `${hw.ram_gb} GB`],
              ["CPU",  hw.cpu_name],
              ["NPU",  hw.npu_name ?? "None detected"],
              ["OS",   hw.os],
              ["Tier", hw.tier.toUpperCase()],
              ["Engines", [hw.has_ollama && "Ollama", hw.has_llama_cpp && "llama.cpp", hw.has_mlx && "MLX"].filter(Boolean).join(" · ") || "None detected"],
            ] as [string,string][]).map(([k,v]) => (
              <div key={k} style={{ display: "flex", justifyContent: "space-between", padding: "5px 0", borderBottom: "1px solid var(--bg-raised)", fontSize: 11 }}>
                <span style={{ color: "var(--text-muted)" }}>{k}</span>
                <span className="mono" style={{ color: "var(--text)", fontSize: 11 }}>{v}</span>
              </div>
            ))}
          </>
        ) : (
          <div style={{ fontSize: 11, color: "var(--text-faint)" }}>Hardware profile not available.</div>
        )}
        <div style={{ marginTop: 14, display: "flex", gap: 8, flexWrap: "wrap" }}>
          <Btn variant="ghost" onClick={rescanHardware}>↺ Rescan Hardware</Btn>
          <Btn
            variant="info"
            onClick={handleReconfigure}
            disabled={reconfiguring}
            style={{ opacity: reconfiguring ? 0.6 : 1 }}
          >
            {reconfiguring ? "Reconfiguring…" : "⚙ Reconfigure Models"}
          </Btn>
        </div>
      </div>

      {/* Behavior */}
      <SectionLabel>BEHAVIOR</SectionLabel>
      <ConfigBlock>
        <ConfigRow label="Background mode"><Toggle value={settings.background_mode} onChange={(v) => set("background_mode")(v)} /></ConfigRow>
        <ConfigRow label="Push notifications"><Toggle value={settings.push_notifications} onChange={(v) => set("push_notifications")(v)} /></ConfigRow>
        <ConfigRow label="Notify: batch complete"><Toggle value={settings.notify_batch_complete} onChange={(v) => set("notify_batch_complete")(v)} /></ConfigRow>
        <ConfigRow label="Notify: errors"><Toggle value={settings.notify_errors} onChange={(v) => set("notify_errors")(v)} /></ConfigRow>
        <ConfigRow label="Notify: pipeline done"><Toggle value={settings.notify_pipeline_done} onChange={(v) => set("notify_pipeline_done")(v)} /></ConfigRow>
      </ConfigBlock>

      <SectionLabel>STORAGE</SectionLabel>
      <ConfigBlock>
        <ConfigRow label="Dataset path">
          <FieldInput defaultValue={settings.storage_path} onBlur={(e) => set("storage_path")(e.target.value)} style={{ width: 200, textAlign: "right" }} />
        </ConfigRow>
        <ConfigRow label="Export path">
          <FieldInput defaultValue={settings.export_path} onBlur={(e) => set("export_path")(e.target.value)} style={{ width: 200, textAlign: "right" }} />
        </ConfigRow>
      </ConfigBlock>
    </div>
  );
}

export function SettingsScreen() {
  const { settingsTab, settingsLoaded, loadSettings } = useStore();

  useEffect(() => { if (!settingsLoaded) loadSettings(); }, []);

  const panels: Record<SettingsTab, React.ReactNode> = {
    Pipeline:     <PipelineSettings />,
    Models:       <ModelsSettings />,
    Verification: <VerificationSettings />,
    Generation:   <GenerationSettings />,
    APIs:         <APISettings />,
    System:       <SystemSettings />,
  };

  return (
    <div style={{ flex: 1, display: "flex", overflow: "hidden" }}>
      <SettingsSidebar />
      <div style={{ flex: 1, overflowY: "auto", padding: 28 }}>
        {!settingsLoaded
          ? <div style={{ fontSize: 12, color: "var(--text-faint)" }}>Loading settings…</div>
          : panels[settingsTab]
        }
      </div>
    </div>
  );
}
