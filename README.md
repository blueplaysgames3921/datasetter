# Datasetter

**AI-augmented synthetic dataset generation, verification, and repair — running on your own desktop.**

Datasetter is a local-first desktop application that turns a prompt, a pile of files, or an existing dataset into a clean, structured, schema-flexible dataset ready for training or evaluation. A coordinated team of eight agents handles interpretation, research, generation, linting, verification, and automatic repair — with every step streamed to the UI in real time.

It runs as a Tauri desktop shell around a Python (FastAPI) sidecar, so it installs like a normal app but does real agentic work under the hood, mixing cloud models with local inference depending on what each step actually needs.

---

## Why Datasetter

Most "generate me a dataset" workflows are a single prompt to a single model, with no verification step and no way to fix what comes out wrong. Datasetter is built around three ideas instead:

- **Verification isn't optional.** Every row is checked for semantic coherence, logical consistency, constraint violations, tone/register consistency, format correctness, and length — then automatically repaired if it fails, with bounded retry loops so a bad batch doesn't run forever.
- **Not every step needs a frontier model.** Cheap, fast, local models are well-suited to verification and file analysis; only a few steps (initial interpretation, complex research, multi-step generation) benefit from a stronger cloud model. Datasetter routes accordingly and adapts to your hardware automatically.
- **Datasets aren't always instruction/response pairs.** The schema is whatever you need — flat key-value rows, multi-turn conversations, classification labels, anything — because the pipeline doesn't assume a fixed shape.

---

## How It Works

```
User Prompt + Files
      │
      ▼
  Orchestrator   ◄─── coordinates every agent, manages retries, pause/resume, failures
      │
      ▼
  Analyser       ── (if files attached) reads attachments, extracts usable context
      │
      ▼
  Interpreter    ── parses intent, scopes the job, decides which pipeline mode applies
      │
   ┌──┴──────────────────────┐
   │  (if internet needed)   │
   ▼                         │
Researcher ───────────────┐  │
  (Gemini + web grounding) │  │
                          ▼  ▼
                       Generator   ── produces seed examples, category targets, blueprint
                               │
                               ▼
                            Scripter    ── generates the full dataset in batches, lints per category
                               │
                               ▼
                            Verifier    ── checks semantic, logic, constraint, consistency, format, length
                               │
                          (on errors)
                               ▼
                             Fixer      ── targeted structured fixes, tool-native small model
                               │
                         (re-verify, bounded retry loop)
                               ▼
                            Verifier    ── re-checks fixed rows
```

Every agent status change, row update, and batch completion streams to the frontend over Server-Sent Events — no polling, and you can watch the dataset build in real time.

### Pipeline modes (auto-detected by the Interpreter)

| Mode | Trigger | Active agents |
|---|---|---|
| **Vibe** | Prompt only, no files or research | Interpreter → Generator → Scripter → Verifier |
| **File** | Files or images attached | Analyser → Interpreter → Generator → Scripter → Verifier |
| **Research** | Job needs current/external information | Interpreter → Researcher → Generator → Scripter → Verifier |
| **Edit** | Modifying an existing dataset | Analyser → Interpreter → Scripter → Verifier |
| **Minimal** | Small, simple job | Interpreter → Scripter → Verifier |

---

## The Agents

| Agent | Role |
|---|---|
| **Orchestrator** | State machine coordinating the whole run — agent sequencing, Fixer↔Verifier retry loops, pause/resume, failure handling |
| **Interpreter** | Parses the prompt, scopes the job, selects a pipeline mode, produces the `GenerationSpec` |
| **Analyser** | Reads attached files, images, or existing datasets to extract usable context (runs locally — too costly to run on cloud at scale) |
| **Researcher** | Gemini-powered web-grounded retrieval for jobs that need current or factual information (cloud only — needs live web access) |
| **Generator** | Produces a `SeedPack`: representative seed examples, per-category targets, and a natural-language blueprint the rest of the pipeline follows |
| **Scripter** | Generates the full dataset in batches against the blueprint, with per-category linting as it goes |
| **Verifier** | Row-by-row quality control across six error types: semantic, logic, constraint, consistency, format, length |
| **Fixer** | Applies targeted, structured fixes to flagged rows using a small tool-native model, guided directly by the Verifier's `fix_instruction` |

Error reports use a structured taxonomy (`error_type`, `field`, `description`, `fix_instruction`, `severity`) so the Fixer never has to interpret free text — it acts on explicit instructions.

---

## Hybrid Cloud / Local Inference

Datasetter scans your hardware on first run (GPU, VRAM, RAM) and assigns a tier — `low`, `mid`, `high`, or `ultra` — which sets sensible model defaults per agent. You can override anything afterward.

The rule of thumb baked into the defaults:

- **Local-preferred:** Analyser, Verifier, Fixer — high-volume, repetitive, well-suited to small/cheap models
- **Cloud-only:** Researcher — needs live, grounded web access that local models can't provide
- **Configurable either way:** Orchestrator, Interpreter, Generator, Scripter — choose based on your hardware and budget

Local inference is served through Ollama, llama.cpp, or MLX. Cloud inference is routed through [LiteLLM](https://github.com/BerriAI/litellm), giving you a single integration point across providers.

---

## Output Formats

Export to JSONL, CSV, TSV, JSON, Parquet, Arrow, XML, or XLSX. Because `DatasetRow.fields` is schema-flexible (`Dict[str, Any]`), Datasetter isn't locked to instruction/response pairs — rows can carry whatever fields your use case needs, and export handles varying schemas across rows correctly.

---

## Quality Controls

- **Six-part verification** per row: semantic, logic, constraint, consistency, format, length — each independently toggleable
- **Bounded Fixer↔Verifier retry loop** — capped at a configurable number of rounds per row (default 3) so a stubborn row doesn't loop forever
- **Error-halt threshold** — the pipeline automatically pauses if too large a share of a batch is failing verification (default 10%), so you're alerted before burning budget on a broken run
- **Diversity control** — a 1–5 diversity dial influences how varied the Generator's seed examples and blueprint guidance are

---

## Project Layout

```
datasetter/
├── src-py/                   Python sidecar — all AI logic, spawned by Tauri
│   ├── main.py                FastAPI server, IPC bridge, SSE endpoints
│   ├── agents/                 The eight agents described above
│   ├── core/
│   │   ├── config_agent.py     Hardware-aware model assignment & safe defaults
│   │   ├── models.py           Pydantic schemas (JobConfig, DatasetRow, SeedPack, etc.)
│   │   ├── optimizations.py    Prompt caching, context trimming, speculative decode
│   │   └── litellm_router.py   Unified LLM routing across cloud + local providers
│   └── utils/
│       ├── hardware.py          GPU / RAM / NPU detection and tiering
│       ├── linter.py            Per-row dataset linting
│       ├── storage.py           Local persistence and multi-format export
│       └── events.py            SSE event emitter
├── src-ui/                   React + TypeScript frontend (Zustand state, Vite)
├── src-tauri/                 Tauri shell — kept thin, just IPC + window management
└── package.json               Root scripts (dev, build, setup)
```

---

## Getting Started

```bash
# Install everything (Python deps + UI deps)
npm run setup

# Run the full desktop app
npm run dev

# Or run pieces independently:
npm run py:dev      # Python sidecar only
npm run ui:dev       # Frontend only (Vite dev server)
```

See `SETUP.md` for hardware tiering details, local model recommendations, and full configuration instructions.

---

## License

See `LICENSE`. This is proprietary, source-available software — free to use, but not free to copy, redistribute, or modify without permission. See `TERMS.md`, `PRIVACY.md`, and `DISCLAIMER.md` for the full terms governing use of the Software and any datasets you generate with it.
