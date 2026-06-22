// src-ui/src/lib/agents.ts
// Static metadata for all agents. Used across multiple components.

import type { AgentID, AgentMeta } from "../types";

export const AGENT_META: AgentMeta[] = [
  {
    id:    "orchestrator",
    label: "Orchestrator",
    short: "ORC",
    color: "#f0c040",
    desc:  "Oversees pipeline, manages flow, handles failures and retries.",
  },
  {
    id:    "interpreter",
    label: "Interpreter",
    short: "INT",
    color: "#60c8f0",
    desc:  "Parses intent, scopes the job, decides active agents, writes generation spec.",
  },
  {
    id:    "analyser",
    label: "Analyser",
    short: "IDX",
    color: "#a78bfa",
    desc:  "Analyses attached files, images, videos, datasets. Extracts constraints and prerequisites.",
  },
  {
    id:    "researcher",
    label: "Researcher",
    short: "RES",
    color: "#34d399",
    desc:  "Retrieves live information from the internet via Gemini. Activated by Interpreter.",
  },
  {
    id:    "generator",
    label: "Generator",
    short: "GEN",
    color: "#f97316",
    desc:  "Two-pass seed generation: maps categories then generates up to 150 diverse seeds.",
  },
  {
    id:    "scripter",
    label: "Scripter",
    short: "SCR",
    color: "#f43f5e",
    desc:  "Scales seeds to full dataset in isolated container. Lints as it generates, category by category.",
  },
  {
    id:    "verifier",
    label: "Verifier",
    short: "VER",
    color: "#22d3ee",
    desc:  "Verifies every row — semantic, logic, constraint, consistency. Batch or one-by-one.",
  },
  {
    id:    "fixer",
    label: "Fixer",
    short: "FIX",
    color: "#a3e635",
    desc:  "Executes targeted fixes from Verifier error reports. Tool-native SLM — fast and surgical.",
  },
];

export const AGENT_MAP: Record<AgentID, AgentMeta> = Object.fromEntries(
  AGENT_META.map((a) => [a.id, a])
) as Record<AgentID, AgentMeta>;

export function getAgent(id: AgentID): AgentMeta {
  return AGENT_MAP[id] ?? {
    id,
    label: id,
    short: id.slice(0, 3).toUpperCase(),
    color: "#64748b",
    desc:  "",
  };
}
