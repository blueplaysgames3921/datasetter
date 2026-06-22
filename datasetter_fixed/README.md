# Datasetter

AI-augmented dataset generation and verification. Multi-agent pipeline with local/cloud hybrid inference.

## Architecture

```
datasetter/
├── src-py/                  # Python sidecar — all AI logic
│   ├── main.py              # FastAPI server, IPC bridge to Tauri
│   ├── agents/
│   │   ├── orchestrator.py  # Pipeline coordinator
│   │   ├── interpreter.py   # Intent parsing, spec generation
│   │   ├── analyser.py      # File/image/dataset analysis
│   │   ├── researcher.py    # Gemini web retrieval
│   │   ├── generator.py     # Seed example generation
│   │   ├── scripter.py      # Full dataset generation + linting
│   │   ├── verifier.py      # Row-by-row verification
│   │   └── fixer.py         # Targeted error fixing
│   ├── core/
│   │   ├── config_agent.py  # Hardware-aware model assignment & defaults
│   │   ├── models.py        # Pydantic schemas
│   │   ├── optimizations.py # Prompt caching, trimming, speculative decode
│   │   └── litellm_router.py# Unified LLM routing via LiteLLM
│   └── utils/
│       ├── hardware.py      # Hardware scanning (GPU, RAM, NPU)
│       ├── linter.py        # Dataset linting utilities
│       ├── storage.py       # Local dataset persistence
│       └── events.py        # SSE event emitter
├── src-ui/                  # React + TypeScript frontend
│   └── src/
│       ├── App.tsx
│       ├── components/      # Shared UI components
│       ├── screens/         # Full page screens
│       ├── hooks/           # React hooks
│       ├── store/           # Zustand state
│       └── types/           # TypeScript types
├── src-tauri/               # Tauri shell (thin — Rust kept minimal)
│   └── src/
│       ├── main.rs
│       └── commands.rs      # Tauri commands bridging UI ↔ Python sidecar
└── package.json             # Root scripts (dev, build, setup)
```

## Agent Pipeline

```
User Prompt + Files
      │
      ▼
  Orchestrator  ◄─── manages everything, handles failures
      │
      ▼
  Analyser     ── (if files attached) reads attachments, extracts context
      │
      ▼
  Interpreter   ── parses intent, scopes job, decides active agents
      │
   ┌──┴──────────────────────┐
   │  (if internet needed)   │
   ▼                         │
Researcher ───────────────┐  │
                          ▼  ▼
                       Generator ◄── Spec + context
                               │
                               ▼
                            Scripter  ── isolated container, lints per category
                               │
                               ▼
                            Verifier  ── semantic, logic, constraints, consistency, format, length
                               │
                          (on errors)
                               ▼
                             Fixer    ── tool-native SLM, structured fixes
                               │
                         (re-verify)
                               ▼
                            Verifier  ── re-check fixed rows
```

## Pipelines (auto-detected by Interpreter)

| Pipeline | Trigger | Flow |
|---|---|---|
| Vibe      | Prompt only | I → G → S → V |
| File/Dataset | Files attached | A → I → G → S → V |
| Research | Internet needed | I → R → G → S → V |
| Edit | Modifying existing dataset | A → I → S → V |
| Minimal | Simple job | I → S → V |

## Model Defaults (6GB VRAM / 16GB RAM baseline)

| Agent | Cloud | Local |
|---|---|---|
| Orchestrator | claude-sonnet-4-6 | Gemma 4 26BA4B (if hardware allows) |
| Interpreter | Step 3.5 Flash / Qwen 3 | LFM 2 9BA1B |
| Analyser | — (cloud too costly) | Gemma 4 E4B Q4 / LFM 6B+ Q4 |
| Researcher | gemini-2.5-pro | — (cloud only) |
| Generator | Llama 3.3 70B | Gemma 3 12B Q4 |
| Scripter | claude-haiku-4-5 / Kimi K2 | — |
| Verifier | — (local preferred) | LFM 4B / Gemma 4 E2B |
| Fixer | — | Qwen2.5 1.5B (tool-native) |

## Setup

```bash
# Python sidecar
cd src-py
pip install -r requirements.txt
python main.py

# Frontend (dev)
cd src-ui
npm install
npm run dev

# Full app (Tauri)
npm run dev
```
