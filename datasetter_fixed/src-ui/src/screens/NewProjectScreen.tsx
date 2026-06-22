// src-ui/src/screens/NewProjectScreen.tsx
//
// Three-step wizard:
//   Step 0 — Describe: prompt textarea + file drop zone
//   Step 1 — Configure: format, rows, verification, constraints
//   Step 2 — Review & Launch
//
// No manual pipeline selection — Interpreter decides.
// Files attached → Analyser activates automatically.

import React, { useRef, useState } from "react";
import {
  AgentBadge, Btn, ConfigBlock, ConfigRow,
  FieldInput, FieldTextarea, SectionLabel, SimpleSelect,
} from "../components";
import { openFileDialog } from "../lib/api";
import { defaultJobConfig, uuid } from "../lib/api";
import { AGENT_META } from "../lib/agents";
import { useStore } from "../store";
import type { AgentID, FieldConstraint, JobConfig, OutputFormat, VerifyMode } from "../types";

// ── Constants ─────────────────────────────────────────────────────────────────

const FORMATS: OutputFormat[] = ["jsonl","csv","parquet","tsv","json","arrow","xml","xlsx"];
const USECASES = [
  "Fine-tuning LLM","RAG / Retrieval","Classification","NER",
  "Summarization","Q&A","Code generation","Custom",
];
const LANGUAGES = ["English","Spanish","French","German","Chinese","Japanese","Arabic","Portuguese","Custom"];

// Pipeline agents shown per mode (informational only)
const PIPELINE_AGENTS: Record<string, AgentID[]> = {
  vibe:     ["interpreter","generator","scripter","verifier","fixer"],
  file:     ["analyser","interpreter","generator","scripter","verifier","fixer"],
  research: ["interpreter","researcher","generator","scripter","verifier","fixer"],
  edit:     ["analyser","interpreter","scripter","verifier","fixer"],
  minimal:  ["interpreter","scripter","verifier","fixer"],
};

// ── Step indicator ────────────────────────────────────────────────────────────

function StepBar({ step, onBack }: { step: number; onBack: (s: number) => void }) {
  const steps = ["Describe", "Configure", "Review & Launch"];
  return (
    <div style={{
      display: "flex", alignItems: "center",
      padding: "14px 28px", borderBottom: "1px solid var(--border-dim)",
      gap: 0, flexShrink: 0,
    }}>
      {steps.map((s, i) => (
        <React.Fragment key={s}>
          <div
            style={{ display: "flex", alignItems: "center", gap: 8, cursor: i < step ? "pointer" : "default" }}
            onClick={() => i < step && onBack(i)}
          >
            <div style={{
              width: 26, height: 26, borderRadius: 13,
              display: "flex", alignItems: "center", justifyContent: "center",
              fontSize: 11, fontWeight: 700, transition: "all .2s",
              background: i === step ? "var(--accent)" : i < step ? "#f0c04025" : "var(--bg-raised)",
              color: i === step ? "#080c14" : i < step ? "var(--accent)" : "var(--text-faint)",
              border: `1px solid ${i <= step ? "var(--accent)" : "var(--border)"}`,
            }}>
              {i < step ? "✓" : i + 1}
            </div>
            <span style={{
              fontSize: 12, fontWeight: 600,
              color: i === step ? "var(--text)" : i < step ? "#60c8f0" : "var(--text-faint)",
            }}>
              {s}
            </span>
          </div>
          {i < steps.length - 1 && (
            <div style={{ width: 40, height: 1, background: i < step ? "#f0c04040" : "var(--border)", margin: "0 12px" }} />
          )}
        </React.Fragment>
      ))}
      <div style={{ flex: 1 }} />
    </div>
  );
}

// ── Step 0: Describe ──────────────────────────────────────────────────────────

function DescribeStep({
  prompt, setPrompt,
  files, setFiles,
  onNext,
}: {
  prompt: string; setPrompt: (s: string) => void;
  files: string[]; setFiles: (f: string[]) => void;
  onNext: () => void;
}) {
  const [dragOver, setDragOver] = useState(false);

  const detectedMode = files.length > 0 ? "file" : "vibe";
  const modeDesc: Record<string, string> = {
    vibe:     "I → G → S → V  (prompt only)",
    file:     "A → I → G → S → V  (files attached)",
    research: "I → R → G → S → V  (live data needed)",
    edit:     "A → I → S → V  (modifying existing dataset)",
    minimal:  "I → S → V  (simple job)",
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    const dropped = Array.from(e.dataTransfer.files).map((f) => f.name);
    setFiles([...files, ...dropped]);
  };

  const handleBrowse = async () => {
    const result = await openFileDialog({
      title: "Attach files",
      multiple: true,
      filters: [
        { name: "Datasets", extensions: ["csv","jsonl","json","parquet","tsv","xlsx"] },
        { name: "Documents", extensions: ["pdf","txt","md"] },
        { name: "Images",    extensions: ["png","jpg","jpeg","webp"] },
        { name: "All files", extensions: ["*"] },
      ],
    });
    if (!result.cancelled && result.paths.length > 0) {
      setFiles([...files, ...result.paths]);
    }
  };

  return (
    <div style={{ maxWidth: 680, margin: "0 auto", padding: "0 28px" }}>
      <div style={{ fontSize: 24, fontWeight: 800, marginBottom: 6, letterSpacing: "-.01em" }}>
        What dataset do you want to build?
      </div>
      <div style={{ fontSize: 13, color: "var(--text-muted)", marginBottom: 28 }}>
        Describe it in plain language. The Interpreter figures out the rest.
      </div>

      <FieldTextarea
        value={prompt}
        onChange={(e) => setPrompt(e.target.value)}
        rows={7}
        placeholder={
          "Examples:\n" +
          "• '500 customer support conversations, frustrated users, 5 product categories'\n" +
          "• 'Q&A pairs explaining ML concepts at beginner level, JSONL format'\n" +
          "• 'Python coding challenges with solutions and difficulty tags, 1000 rows'"
        }
        style={{ marginBottom: 14 }}
      />

      {/* File drop zone */}
      <div
        onDrop={handleDrop}
        onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
        onDragLeave={() => setDragOver(false)}
        onClick={handleBrowse}
        style={{
          border: `1px dashed ${dragOver ? "var(--accent)" : "var(--border)"}`,
          borderRadius: 6, padding: 20, textAlign: "center", cursor: "pointer",
          background: dragOver ? "#f0c04008" : "transparent", transition: "all .15s",
          marginBottom: 14,
        }}
      >
        <div style={{ fontSize: 22, opacity: .3, marginBottom: 6 }}>⊕</div>
        <div style={{ fontSize: 12, color: "var(--text-dim)" }}>
          Drop files, datasets, images — or click to browse
        </div>
        <div style={{ fontSize: 10, color: "var(--text-faint)", marginTop: 3 }}>
          CSV · JSONL · Parquet · PDF · images · anything
        </div>
      </div>

      {files.length > 0 && (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 14 }}>
          {files.map((f, i) => (
            <div key={i} style={{
              display: "flex", alignItems: "center", gap: 6,
              background: "var(--bg-raised)", border: "1px solid var(--border)",
              borderRadius: 4, padding: "4px 10px", fontSize: 11,
            }}>
              <span style={{ color: "#a78bfa" }}>⊡</span>
              <span style={{ color: "var(--text-muted)", maxWidth: 200, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {f.split(/[\\/]/).pop() ?? f}
              </span>
              <span
                onClick={(e) => { e.stopPropagation(); setFiles(files.filter((_, j) => j !== i)); }}
                style={{ color: "var(--text-faint)", cursor: "pointer", marginLeft: 2 }}
              >×</span>
            </div>
          ))}
        </div>
      )}

      {/* Auto-detected pipeline */}
      <div style={{
        padding: "10px 14px", borderRadius: 5,
        background: "var(--bg-surface)", border: "1px solid var(--border)",
        fontSize: 11, color: "var(--text-muted)",
        display: "flex", alignItems: "center", gap: 10, marginBottom: 24,
      }}>
        <span style={{ color: "#60c8f0", fontSize: 14 }}>◈</span>
        <span>
          Pipeline auto-detected: <strong style={{ color: "var(--text)" }}>
            {detectedMode.charAt(0).toUpperCase() + detectedMode.slice(1)}
          </strong>
          {" — "}{modeDesc[detectedMode]}
        </span>
      </div>

      <div style={{ display: "flex", justifyContent: "flex-end" }}>
        <Btn
          variant="primary"
          size="md"
          disabled={!prompt.trim()}
          onClick={onNext}
        >
          Configure →
        </Btn>
      </div>
    </div>
  );
}

// ── Step 1: Configure ─────────────────────────────────────────────────────────

function ConfigureStep({
  config, setConfig, onNext, onBack,
}: {
  config: JobConfig;
  setConfig: (c: Partial<JobConfig>) => void;
  onNext: () => void;
  onBack: () => void;
}) {
  const set = (k: keyof JobConfig) => (v: unknown) =>
    setConfig({ [k]: v } as Partial<JobConfig>);

  const cfgInputStyle: React.CSSProperties = {
    background: "var(--bg-base)", border: "1px solid var(--border)",
    borderRadius: 4, color: "var(--text)", padding: "5px 8px",
    fontFamily: "'JetBrains Mono', monospace", fontSize: 11,
    outline: "none", width: 120, textAlign: "right",
  };

  return (
    <div style={{ maxWidth: 680, margin: "0 auto", padding: "0 28px" }}>
      <div style={{ fontSize: 24, fontWeight: 800, marginBottom: 6 }}>Configure the job</div>
      <div style={{ fontSize: 13, color: "var(--text-muted)", marginBottom: 28 }}>
        Tune generation and verification. Interpreter will refine these automatically.
      </div>

      {/* Dataset */}
      <SectionLabel>DATASET</SectionLabel>
      <ConfigBlock>
        <ConfigRow label="Output Format">
          <SimpleSelect
            value={config.output_format}
            onChange={set("output_format")}
            options={FORMATS.map((f) => ({ value: f, label: f.toUpperCase() }))}
          />
        </ConfigRow>
        <ConfigRow label="Use Case">
          <SimpleSelect value={config.use_case || USECASES[0]} onChange={set("use_case")} options={USECASES} />
        </ConfigRow>
        <ConfigRow label="Total Rows">
          <input
            style={cfgInputStyle}
            type="number"
            value={config.total_rows}
            onChange={(e) => set("total_rows")(parseInt(e.target.value) || 500)}
          />
        </ConfigRow>
        <ConfigRow label="Language">
          <SimpleSelect value={config.language} onChange={set("language")} options={LANGUAGES} />
        </ConfigRow>
        <ConfigRow label="Seed Examples (max 150)">
          <input
            style={cfgInputStyle}
            type="number"
            value={config.seed_count}
            onChange={(e) => set("seed_count")(Math.min(150, parseInt(e.target.value) || 50))}
          />
        </ConfigRow>
        <ConfigRow label={`Diversity Level (${config.diversity_level}/5)`}>
          <input
            type="range" min={1} max={5} value={config.diversity_level}
            onChange={(e) => set("diversity_level")(parseInt(e.target.value))}
            style={{ width: 100 }}
          />
        </ConfigRow>
      </ConfigBlock>

      {/* Verification */}
      <SectionLabel>VERIFICATION</SectionLabel>
      <ConfigBlock>
        <ConfigRow label="Mode">
          <div style={{ display: "flex", gap: 6 }}>
            {(["batch", "one_by_one"] as VerifyMode[]).map((m) => (
              <button
                key={m}
                onClick={() => set("verify_mode")(m)}
                style={{
                  padding: "4px 12px", borderRadius: 4, fontSize: 10, fontWeight: 600,
                  cursor: "pointer", fontFamily: "'Syne', sans-serif",
                  background: config.verify_mode === m ? "#22d3ee18" : "transparent",
                  color: config.verify_mode === m ? "#22d3ee" : "var(--text-faint)",
                  border: `1px solid ${config.verify_mode === m ? "#22d3ee" : "var(--border)"}`,
                }}
              >
                {m === "batch" ? "Batch" : "One-by-one"}
              </button>
            ))}
          </div>
        </ConfigRow>
        {config.verify_mode === "batch" && (
          <ConfigRow label="Batch Size">
            <input
              style={cfgInputStyle}
              type="number"
              value={config.batch_size}
              onChange={(e) => set("batch_size")(parseInt(e.target.value) || 50)}
            />
          </ConfigRow>
        )}
        <ConfigRow label={`Strictness (${config.strictness}/5)`}>
          <input
            type="range" min={1} max={5} value={config.strictness}
            onChange={(e) => set("strictness")(parseInt(e.target.value))}
            style={{ width: 100 }}
          />
        </ConfigRow>
        <ConfigRow label="Auto-fix errors">
          <input
            type="checkbox" checked={config.auto_fix}
            onChange={(e) => set("auto_fix")(e.target.checked)}
            style={{ cursor: "pointer" }}
          />
        </ConfigRow>
      </ConfigBlock>

      {/* Negative prompting */}
      <SectionLabel>NEGATIVE PROMPTING</SectionLabel>
      <FieldTextarea
        value={config.negative_prompt}
        onChange={(e) => set("negative_prompt")(e.target.value)}
        rows={3}
        placeholder="Describe what to avoid in generated examples…"
        style={{ marginBottom: 22 }}
      />

      {/* Extra context */}
      <SectionLabel>EXTRA CONTEXT (OPTIONAL)</SectionLabel>
      <FieldTextarea
        value={config.extra_context}
        onChange={(e) => set("extra_context")(e.target.value)}
        rows={3}
        placeholder="Additional domain knowledge, constraints, or references for all agents…"
        style={{ marginBottom: 28 }}
      />

      <div style={{ display: "flex", justifyContent: "space-between" }}>
        <Btn variant="ghost" onClick={onBack}>← Back</Btn>
        <Btn variant="primary" size="md" onClick={onNext}>Review →</Btn>
      </div>
    </div>
  );
}

// ── Step 2: Review ────────────────────────────────────────────────────────────

function ReviewStep({
  config, files, onLaunch, onBack, launching,
}: {
  config: JobConfig;
  files: string[];
  onLaunch: () => void;
  onBack: () => void;
  launching: boolean;
}) {
  const detectedMode = files.length > 0 ? "file" : "vibe";
  const agents = PIPELINE_AGENTS[detectedMode] ?? PIPELINE_AGENTS.vibe;

  const summaryRows: [string, string][] = [
    ["Format",       config.output_format.toUpperCase()],
    ["Use Case",     config.use_case || "—"],
    ["Total Rows",   String(config.total_rows)],
    ["Language",     config.language],
    ["Seeds",        String(config.seed_count)],
    ["Diversity",    `${config.diversity_level}/5`],
    ["Verification", config.verify_mode === "batch"
      ? `Batch · ${config.batch_size}/batch`
      : "One-by-one"],
    ["Strictness",   `${config.strictness}/5`],
    ["Auto-fix",     config.auto_fix ? "Yes" : "No"],
    ...(files.length ? [["Files", `${files.length} attached`] as [string, string]] : []),
    ...(config.negative_prompt ? [["Negative Prompt", "set"] as [string, string]] : []),
  ];

  return (
    <div style={{ maxWidth: 640, margin: "0 auto", padding: "0 28px" }}>
      <div style={{ fontSize: 24, fontWeight: 800, marginBottom: 6 }}>Review & Launch</div>
      <div style={{ fontSize: 13, color: "var(--text-muted)", marginBottom: 24 }}>
        Confirm before the pipeline starts.
      </div>

      {/* Prompt */}
      <div style={{
        background: "var(--bg-surface)", border: "1px solid var(--border)",
        borderRadius: 6, padding: 14, marginBottom: 12,
      }}>
        <div style={{ fontSize: 9, fontWeight: 700, letterSpacing: ".12em", color: "var(--text-faint)", marginBottom: 8 }}>
          PROMPT
        </div>
        <div className="mono" style={{ fontSize: 12, color: "var(--text-muted)", lineHeight: 1.65 }}>
          {config.prompt}
        </div>
      </div>

      {/* Summary */}
      <div style={{
        background: "var(--bg-surface)", border: "1px solid var(--border)",
        borderRadius: 6, padding: 14, marginBottom: 12,
      }}>
        <div style={{ fontSize: 9, fontWeight: 700, letterSpacing: ".12em", color: "var(--text-faint)", marginBottom: 10 }}>
          JOB SUMMARY
        </div>
        {summaryRows.map(([k, v]) => (
          <div key={k} style={{
            display: "flex", justifyContent: "space-between",
            padding: "5px 0", borderBottom: "1px solid var(--bg-raised)", fontSize: 12,
          }}>
            <span style={{ color: "var(--text-muted)" }}>{k}</span>
            <span className="mono" style={{ color: "var(--text)", fontSize: 11 }}>{v}</span>
          </div>
        ))}
      </div>

      {/* Active agents */}
      <div style={{
        background: "var(--bg-surface)", border: "1px solid var(--border)",
        borderRadius: 6, padding: 14, marginBottom: 28,
      }}>
        <div style={{ fontSize: 9, fontWeight: 700, letterSpacing: ".12em", color: "var(--text-faint)", marginBottom: 10 }}>
          ESTIMATED ACTIVE AGENTS
        </div>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
          {agents.map((id) => <AgentBadge key={id} id={id} size="md" />)}
        </div>
        <div style={{ fontSize: 10, color: "var(--text-faint)", marginTop: 8 }}>
          Interpreter may activate or skip agents based on your prompt.
        </div>
      </div>

      <div style={{ display: "flex", justifyContent: "space-between" }}>
        <Btn variant="ghost" onClick={onBack} disabled={launching}>← Back</Btn>
        <button
          onClick={onLaunch}
          disabled={launching}
          style={{
            padding: "11px 36px", borderRadius: 5, fontSize: 14, fontWeight: 800,
            cursor: launching ? "not-allowed" : "pointer",
            background: launching ? "var(--bg-raised)" : "var(--accent)",
            color: launching ? "var(--text-muted)" : "#080c14",
            border: "none", fontFamily: "'Syne', sans-serif", letterSpacing: ".04em",
            transition: "all .15s",
          }}
        >
          {launching ? "Launching…" : "▶ LAUNCH PIPELINE"}
        </button>
      </div>
    </div>
  );
}

// ── Main Screen ───────────────────────────────────────────────────────────────

export function NewProjectScreen() {
  const { setScreen, launchJob, currentConfig } = useStore();

  const [step,      setStep]      = useState(0);
  const [prompt,    setPrompt]    = useState("");
  const [files,     setFiles]     = useState<string[]>([]);
  const [launching, setLaunching] = useState(false);

  const [config, setConfigState] = useState<JobConfig>(() =>
    defaultJobConfig({ name: "New Dataset" })
  );

  const mergeConfig = (partial: Partial<JobConfig>) =>
    setConfigState((prev) => ({ ...prev, ...partial }));

  const handleLaunch = async () => {
    setLaunching(true);
    const finalConfig: JobConfig = {
      ...config,
      job_id:         uuid(),
      prompt,
      attached_files: files,
      name:           prompt.slice(0, 60) || "Untitled Dataset",
    };
    try {
      await launchJob(finalConfig);
    } catch {
      setLaunching(false);
    }
  };

  return (
    <div style={{ flex: 1, display: "flex", flexDirection: "column", overflow: "hidden", background: "var(--bg-base)" }}>
      {/* Step bar */}
      <div style={{ display: "flex", alignItems: "center", flexShrink: 0 }}>
        <StepBar step={step} onBack={setStep} />
        <button
          onClick={() => setScreen(currentConfig ? "workspace" : "home")}
          style={{ background: "none", border: "none", color: "var(--text-faint)", cursor: "pointer", fontSize: 20, padding: "0 20px 0 0" }}
        >×</button>
      </div>

      <div style={{ flex: 1, overflowY: "auto", padding: "32px 0" }}>
        {step === 0 && (
          <DescribeStep
            prompt={prompt} setPrompt={setPrompt}
            files={files}   setFiles={setFiles}
            onNext={() => {
              mergeConfig({ prompt, attached_files: files });
              setStep(1);
            }}
          />
        )}
        {step === 1 && (
          <ConfigureStep
            config={config}
            setConfig={mergeConfig}
            onNext={() => setStep(2)}
            onBack={() => setStep(0)}
          />
        )}
        {step === 2 && (
          <ReviewStep
            config={config}
            files={files}
            onLaunch={handleLaunch}
            onBack={() => setStep(1)}
            launching={launching}
          />
        )}
      </div>
    </div>
  );
}
