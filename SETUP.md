# Datasetter — Setup Guide

## Prerequisites

| Tool | Version | Notes |
|---|---|---|
| Python | 3.11+ | 3.12 recommended |
| Node.js | 20+ | LTS recommended |
| Rust | latest stable | `rustup update stable` |
| Tauri CLI v2 | 2.x | installed via npm |

For local model inference (optional but recommended):
- **Ollama** — https://ollama.com — easiest local inference for most hardware
- **llama.cpp** — https://github.com/ggml-org/llama.cpp — CPU-heavy, low VRAM
- **MLX** — Apple Silicon only — `pip install mlx-lm`

---

## Quick Start

### 1. Clone and install

```bash
git clone https://github.com/blueplaysgames3921/datasetter
cd datasetter
npm run setup          # installs Python deps + Node deps
```

### 2. Add API keys

```bash
# Create src-py/.env and add at minimum:
echo 'ANTHROPIC_API_KEY=sk-ant-...' > src-py/.env
echo 'GOOGLE_API_KEY=AIza...'       >> src-py/.env
# (or open src-py/.env in a text editor and fill in your keys)
```

Or configure keys through the app UI → Settings → APIs after first launch.

### 3. Install local models (optional)

```bash
# Verifier (recommended local — saves cost on large datasets)
ollama pull lfm:4b

# Fixer (tiny, fast)
ollama pull qwen2.5:1.5b

# Generator (if you want local generation)
ollama pull gemma3:12b
```

### 4. Run in development

```bash
npm run dev            # starts Tauri (spawns Python sidecar + opens window)
```

Or run frontend and backend separately:

```bash
# Terminal 1 — Python sidecar
npm run py:dev

# Terminal 2 — Vite dev server
npm run ui:dev

# Terminal 3 — Tauri shell (optional in dev — can use browser at localhost:1420)
npx tauri dev
```

### 5. Build for production

```bash
# Bundle Python sidecar with PyInstaller first
cd src-py
pip install pyinstaller
pyinstaller main.py --name main --onefile --distpath ../src-tauri/sidecar/

# Build Tauri app
cd ..
npm run build
# → installer in src-tauri/target/release/bundle/
```

---

## Architecture Overview

```
User
 │
 ▼
Tauri shell (Rust — thin)
 │  spawns
 ▼
Python sidecar (FastAPI on localhost:PORT)
 │
 ├── POST /jobs/start ──────────► Orchestrator
 │                                     │
 │                               ┌─────┴──────┐
 │                            Analyser    Interpreter
 │                                     │
 │                               ┌─────┴──────┐
 │                           Researcher   Generator
 │                                     │
 │                                  Scripter
 │                                     │
 │                                  Verifier ◄──┐
 │                                     │         │
 │                                   Fixer ──────┘
 │
 ├── GET /jobs/{id}/events ──────► SSE stream → React frontend
 └── GET/PATCH /jobs/{id}/rows
```

---

## Environment Variables

Create `src-py/.env`:

```env
# Required
ANTHROPIC_API_KEY=sk-ant-...
GOOGLE_API_KEY=AIza...

# Optional cloud providers (configurable in Settings UI too)
DEEPINFRA_API_KEY=
FEATHERLESS_API_KEY=
GROQ_API_KEY=gsk_...
SAMBANOVA_API_KEY=
FIREWORKS_API_KEY=fw_...
NOVITA_API_KEY=
SILICONFLOW_API_KEY=
RUNPOD_API_KEY=

# App
DATASETTER_PORT=57423    # port for the sidecar (random if not set)
```

---

## Default Model Assignments

| Agent | Cloud default | Local default | Mode |
|---|---|---|---|
| Orchestrator | claude-sonnet-4-6 | Gemma 4 26BA4B | Cloud |
| Interpreter | Step 3.5 Flash | LFM 2 9BA1B | Auto |
| Analyser | — | Gemma 4 E4B Q4 | Local |
| Researcher | gemini-2.5-pro | — | Cloud |
| Generator | Llama 3.3 70B | Gemma 3 12B Q4 | Auto |
| Scripter | claude-haiku-4-5 | — | Cloud |
| Verifier | — | LFM 4B | Local |
| Fixer | — | Qwen2.5 1.5B | Local |

Override any of these in Settings → Models or per-job at launch.

---

## Hardware Tiers

The Configuration Agent scans your hardware on first launch and sets defaults:

| Tier | Hardware | What runs locally |
|---|---|---|
| low | No GPU / <4GB VRAM | Almost nothing — cloud-heavy |
| mid | 4–8GB VRAM, 8–16GB RAM | Fixer, Verifier (4B models) |
| high | 8–16GB VRAM, 16–32GB RAM | + Analyser, Generator (12B) |
| ultra | 24GB+ VRAM / Apple M | Everything locally if desired |

---

## Supported Output Formats

JSONL · CSV · Parquet · TSV · JSON · Arrow · XLSX · XML

---

## Supported Cloud Providers

Via LiteLLM:
- Anthropic (Claude)
- Google (Gemini) — also powers Researcher
- Pollinations.ai (free tier, wide model diversity)
- DeepInfra
- Featherless
- Groq
- SambaNova
- Fireworks
- Novita
- SiliconFlow
- RunPod / Vast.ai
- Any OpenAI-compatible endpoint (personal servers, Ollama remote, etc.)

---

## Troubleshooting

**Sidecar won't start**
- Check Python version: `python --version` (needs 3.11+)
- Check deps: `cd src-py && pip install -r requirements.txt`
- Check port conflict: set `DATASETTER_PORT=57424` in `.env`

**Local model not loading**
- Check Ollama is running: `ollama list`
- Pull the model: `ollama pull <model-name>`
- Check VRAM: open Settings → System → Hardware Profile

**Verifier crashing with OOM**
- Reduce batch size in Settings → Verification
- Lower KV cache clear interval (e.g. 25 rows instead of 50)
- Switch Verifier to a smaller model

**Pipeline stuck / not responding**
- Check sidecar logs: `~/datasetter/logs/sidecar.log`
- Stop the job from the UI, check History → reopen and resume

---

## Project Storage

All data lives locally at `~/datasetter/`:

```
~/datasetter/
├── settings.json
├── hardware_profile.json
├── logs/
│   └── sidecar.log
└── projects/
    └── {job-id}/
        ├── job.json        ← JobConfig
        ├── state.json      ← PipelineState snapshot
        ├── rows.jsonl      ← All DatasetRows (append-friendly)
        ├── seeds.json      ← SeedPack from Generator
        └── exports/
            └── export_{ts}.jsonl
```
