"""
core/config_agent.py

The Configuration Agent runs on first launch or when the user triggers a rescan.
It is NOT part of the generation pipeline — it runs once and writes settings.json.

Engine selection rules (from hardware mapping):
  Windows + Copilot+/NPU      → LiteRT-LM / ONNX Runtime
  Windows + Dedicated GPU     → Ollama (CUDA/ROCm, easiest API)
  Windows + Integrated/CPU    → llama.cpp (AVX2/AVX512, GGUF)
  macOS   + Apple Silicon     → MLX (unified memory, highest throughput)
  Linux   + Dedicated GPU     → Ollama (CUDA/ROCm)
  Linux   + CPU-only          → llama.cpp

Warm model efficiency principles applied:
  - Minimise distinct warm models: where two agents have similar profiles,
    assign the same model so Ollama/MLX keeps one instance warm.
  - Verifier and Fixer share a model family (both tiny, local-only).
  - Analyser and Verifier can share the same local model family on low-VRAM
    hardware (run sequentially, not simultaneously).

Quantization tiers:
  INT8  (Q8)  — baseline standard for most tasks if VRAM allows
  INT4  (Q4)  — budget/constrained hardware default
  INT3/INT2   — ultra-low resource only, explicit fallback

Speculative decoding pairing:
  Where a large target model is used (Generator/Orchestrator local),
  a draft model from the same family is assigned.
"""

from __future__ import annotations

import json
import logging
import platform
from typing import Optional

import litellm

from core.models import (
    AgentID,
    AppSettings,
    HardwareProfile,
    InferenceMode,
    ModelAssignment,
    ModelConfig,
)
from utils.hardware import check_model_compatibility

log = logging.getLogger(__name__)


# ─── OS detection ─────────────────────────────────────────────────────────────

def _detect_os_family(hardware: HardwareProfile) -> str:
    """
    Return a normalised OS family string used for engine selection.
    'windows' | 'macos_apple' | 'macos_intel' | 'linux'
    """
    os_str = (hardware.os or platform.system()).lower()
    if "windows" in os_str:
        return "windows"
    if "darwin" in os_str or "macos" in os_str or "mac os" in os_str:
        vendor = hardware.gpu.vendor if hardware.gpu else "unknown"
        return "macos_apple" if vendor == "apple" else "macos_intel"
    return "linux"


# ─── Engine selection ─────────────────────────────────────────────────────────

def _select_engine(hardware: HardwareProfile) -> tuple[str, str]:
    """
    Select the best inference engine and quantization tier for this hardware.

    Returns:
        (engine_name, quant_suffix)
        engine_name: "ollama" | "llama_cpp" | "mlx" | "onnx"
        quant_suffix: ":q8_0" | ":q4_K_M" | ":q3_K_M" | ""
    """
    os_family = _detect_os_family(hardware)
    vram      = hardware.gpu.vram_gb if hardware.gpu else 0.0
    ram       = hardware.ram_gb
    has_gpu   = hardware.gpu is not None
    has_npu   = hardware.has_npu

    # macOS Apple Silicon — MLX always wins
    if os_family == "macos_apple":
        # Unified memory — use RAM as effective VRAM
        effective = ram
        quant = ":q8_0" if effective >= 24 else ":q4_K_M" if effective >= 8 else ":q3_K_M"
        return "mlx", quant

    # Windows with NPU (Copilot+ AI PCs) — ONNX Runtime
    if os_family == "windows" and has_npu and hardware.has_onnx_runtime:
        quant = ":q8_0" if vram >= 8 or ram >= 32 else ":q4_K_M"
        return "onnx", quant

    # Windows or Linux with dedicated GPU — Ollama
    if has_gpu and vram >= 4 and (hardware.has_ollama):
        quant = ":q8_0" if vram >= 16 else ":q4_K_M" if vram >= 6 else ":q3_K_M"
        return "ollama", quant

    # CPU-only or very low VRAM — llama.cpp
    if hardware.has_llama_cpp:
        quant = ":q4_K_M" if ram >= 8 else ":q3_K_M"
        return "llama_cpp", quant

    # Fallback: Ollama if installed, else llama.cpp
    if hardware.has_ollama:
        return "ollama", ":q4_K_M"
    return "llama_cpp", ":q4_K_M"


def _engine_model(base: str, engine: str, quant: str) -> str:
    """
    Format a model name for the selected engine.
    MLX uses hyphens and a different naming convention.
    ONNX uses the base name only.
    llama.cpp / Ollama use the standard ollama tag format.
    """
    if engine == "mlx":
        # MLX models are pulled from HuggingFace mlx-community
        # e.g. mlx-community/gemma-3-4b-it-4bit
        mlx_names = {
            "gemma3:4b":      "mlx-community/gemma-3-4b-it-4bit",
            "gemma3:12b":     "mlx-community/gemma-3-12b-it-4bit",
            "gemma3:27b":     "mlx-community/gemma-3-27b-it-4bit",
            "qwen2.5:1.5b":   "mlx-community/Qwen2.5-1.5B-Instruct-4bit",
            "qwen2.5:3b":     "mlx-community/Qwen2.5-3B-Instruct-4bit",
            "qwen2.5:7b":     "mlx-community/Qwen2.5-7B-Instruct-4bit",
            "qwen2.5:14b":    "mlx-community/Qwen2.5-14B-Instruct-4bit",
            "qwen2.5:32b":    "mlx-community/Qwen2.5-32B-Instruct-4bit",
            "lfm:4b":         "mlx-community/lfm-40b-4bit",   # best available MLX LFM
        }
        return mlx_names.get(base, f"mlx-community/{base.replace(':', '-')}-4bit")
    elif engine == "onnx":
        # ONNX models referenced by HuggingFace path
        onnx_names = {
            "gemma3:4b":    "google/gemma-3-4b-it",
            "qwen2.5:1.5b": "Qwen/Qwen2.5-1.5B-Instruct",
            "qwen2.5:3b":   "Qwen/Qwen2.5-3B-Instruct",
            "lfm:4b":       "liquid-ai/lfm-40b",
        }
        return onnx_names.get(base, base)
    else:
        # Ollama / llama.cpp: use the tag format
        # quant is already appended in the base when needed
        return base + quant


# ─── Warm model consolidation ─────────────────────────────────────────────────

def _consolidate_models(
    assignments: dict[AgentID, ModelAssignment],
    engine: str,
    quant: str,
    hardware: HardwareProfile,
) -> dict[AgentID, ModelAssignment]:
    """
    Apply warm model consolidation:
    Where two local agents share the same hardware constraints,
    assign the same model so the inference server keeps one warm instance.

    On low-VRAM hardware (mid tier), Verifier and Fixer share the same
    tiny model. Analyser can also use the same tiny model since it runs
    before Verifier (sequential, not concurrent).

    On high-VRAM hardware, keep distinct models for quality.
    """
    tier = hardware.tier

    if tier in ("low", "mid"):
        # Consolidate: Analyser, Verifier, Fixer all use the same tiny model
        tiny_base = "qwen2.5:1.5b" if engine != "mlx" else "qwen2.5:1.5b"
        tiny_model = _engine_model(tiny_base, engine, quant)

        for agent_id in (AgentID.ANALYSER, AgentID.VERIFIER, AgentID.FIXER):
            if agent_id in assignments and assignments[agent_id].local_model:
                assignments[agent_id].local_model = tiny_model
                assignments[agent_id].local_engine = engine
                log.debug(f"Consolidated {agent_id} → {tiny_model} (warm model efficiency)")

    return assignments


# ─── LFM preference for Verifier ──────────────────────────────────────────────

def _prefer_lfm_for_verifier(
    assignment: ModelAssignment,
    engine: str,
    quant: str,
    hardware: HardwareProfile,
) -> ModelAssignment:
    """
    Prefer LFM (Liquid Foundation Models) for the Verifier agent because:
    - LFMs use SSM/Mamba architecture — no KV cache growth
    - KV cache grows O(n) with sequence length for Transformers
    - Verifier processes thousands of rows — KV cache OOM is a real risk
    - LFMs maintain constant memory regardless of context length

    Falls back to smallest Transformer if LFM isn't available.
    """
    vram = hardware.gpu.vram_gb if hardware.gpu else 0.0
    ram  = hardware.ram_gb
    effective = ram if (hardware.gpu and hardware.gpu.vendor == "apple") else vram

    if engine == "mlx":
        # LFM MLX weights exist but are large — use Qwen on low VRAM
        if effective >= 12:
            assignment.local_model = "mlx-community/lfm-40b-4bit"
        else:
            assignment.local_model = _engine_model("qwen2.5:3b", engine, quant)
    elif engine in ("ollama", "llama_cpp"):
        # LFM 4B is the sweet spot — no KV cache, fits in 3GB VRAM
        if effective >= 3:
            assignment.local_model = "lfm:4b"
            assignment.local_engine = engine
        elif effective >= 1.5:
            assignment.local_model = _engine_model("qwen2.5:1.5b", engine, quant)
            assignment.local_engine = engine
        else:
            # Ultra-low: must use cloud (but we enforce local-only for Verifier)
            # If truly no local option, log a hard warning
            log.warning(
                "Verifier: insufficient VRAM for any local model. "
                "Forcing qwen2.5:1.5b — may be slow."
            )
            assignment.local_model  = "qwen2.5:1.5b"
            assignment.local_engine = engine
    else:
        # ONNX: use LFM if available
        assignment.local_model  = "liquid-ai/lfm-40b"
        assignment.local_engine = "onnx"

    return assignment


# ─── Safe defaults (deterministic, no LLM call) ───────────────────────────────

def _safe_defaults(hw: HardwareProfile) -> ModelConfig:
    """
    Deterministic defaults using full OS+hardware decision matrix.
    Used when no API keys are configured or the config agent LLM call fails.
    """
    engine, quant = _select_engine(hw)
    vram          = hw.gpu.vram_gb if hw.gpu else 0.0
    ram           = hw.ram_gb
    effective     = ram if (hw.gpu and hw.gpu.vendor == "apple") else vram
    def local(base: str, min_effective: float) -> Optional[str]:
        """Return formatted model name if hardware meets minimum, else None."""
        return _engine_model(base, engine, quant) if effective >= min_effective else None

    def local_engine_if(base: str, min_effective: float) -> Optional[str]:
        return engine if effective >= min_effective else None

    assignments: dict[AgentID, ModelAssignment] = {

        # ORCHESTRATOR — cloud preferred; strong reasoning required
        AgentID.ORCHESTRATOR: ModelAssignment(
            agent_id=AgentID.ORCHESTRATOR,
            cloud_model="claude-sonnet-4-6",
            cloud_provider="anthropic",
            local_model=local("gemma3:27b", 20.0),
            local_engine=local_engine_if("gemma3:27b", 20.0),
            quantization="q4_K_M" if effective < 24 else "q8_0",
            mode=InferenceMode.CLOUD,
        ),

        # INTERPRETER — cloud fast, local capable; runs once per job
        AgentID.INTERPRETER: ModelAssignment(
            agent_id=AgentID.INTERPRETER,
            cloud_model="gemini/gemini-2.5-flash",
            cloud_provider="google",
            local_model=local("qwen2.5:7b", 5.0),
            local_engine=local_engine_if("qwen2.5:7b", 5.0),
            quantization="q4_K_M" if effective < 8 else "q8_0",
            mode=InferenceMode.AUTO,
        ),

        # ANALYSER — local only; runs before pipeline (sequential with Verifier)
        AgentID.ANALYSER: ModelAssignment(
            agent_id=AgentID.ANALYSER,
            cloud_model=None,
            local_model=local("gemma3:4b", 3.0) or _engine_model("qwen2.5:3b", engine, quant),
            local_engine=engine,
            quantization="q4_K_M" if effective < 6 else "q8_0",
            mode=InferenceMode.LOCAL,
        ),

        # RESEARCHER — Gemini only (search grounding)
        AgentID.RESEARCHER: ModelAssignment(
            agent_id=AgentID.RESEARCHER,
            cloud_model="gemini/gemini-2.5-pro",
            cloud_provider="google",
            local_model=None,
            local_engine=None,
            mode=InferenceMode.CLOUD,
        ),

        # GENERATOR — needs creativity; cloud best, local if capable
        AgentID.GENERATOR: ModelAssignment(
            agent_id=AgentID.GENERATOR,
            cloud_model="deepinfra/meta-llama/Llama-3.3-70B-Instruct",
            cloud_provider="deepinfra",
            local_model=local("gemma3:12b", 8.0),
            local_engine=local_engine_if("gemma3:12b", 8.0),
            quantization="q4_K_M" if effective < 12 else "q8_0",
            mode=InferenceMode.AUTO,
        ),

        # SCRIPTER — high throughput, cost-sensitive; cloud preferred
        AgentID.SCRIPTER: ModelAssignment(
            agent_id=AgentID.SCRIPTER,
            cloud_model="claude-haiku-4-5",
            cloud_provider="anthropic",
            local_model=local("qwen2.5:14b", 10.0),
            local_engine=local_engine_if("qwen2.5:14b", 10.0),
            quantization="q4_K_M" if effective < 14 else "q8_0",
            mode=InferenceMode.CLOUD,
        ),

        # VERIFIER — local only; LFM preferred (no KV cache growth)
        AgentID.VERIFIER: ModelAssignment(
            agent_id=AgentID.VERIFIER,
            cloud_model=None,
            local_model=None,  # filled by _prefer_lfm_for_verifier below
            local_engine=engine,
            quantization="q4_K_M",
            mode=InferenceMode.LOCAL,
        ),

        # FIXER — local only; tool-native SLM; tiny
        AgentID.FIXER: ModelAssignment(
            agent_id=AgentID.FIXER,
            cloud_model=None,
            local_model=_engine_model("qwen2.5:1.5b", engine, quant),
            local_engine=engine,
            quantization="q4_K_M",
            mode=InferenceMode.LOCAL,
        ),
    }

    # Apply LFM preference for Verifier
    assignments[AgentID.VERIFIER] = _prefer_lfm_for_verifier(
        assignments[AgentID.VERIFIER], engine, quant, hw
    )

    # Apply warm model consolidation on low/mid hardware
    assignments = _consolidate_models(assignments, engine, quant, hw)

    # Final fallback: if any LOCAL-mode agent has no local model, switch to cloud
    for agent_id, assignment in assignments.items():
        if assignment.mode == InferenceMode.LOCAL and not assignment.local_model:
            if assignment.cloud_model:
                assignment.mode = InferenceMode.CLOUD
                log.warning(f"{agent_id}: no local model fits — falling back to cloud.")
            else:
                assignment.cloud_model    = "claude-haiku-4-5"
                assignment.cloud_provider = "anthropic"
                assignment.mode           = InferenceMode.CLOUD
                log.warning(f"{agent_id}: no model available — emergency fallback to claude-haiku-4-5.")

    return ModelConfig(assignments=assignments)


# ─── System prompt ────────────────────────────────────────────────────────────

_CONFIG_SYSTEM = """You are the Configuration Agent for Datasetter, an AI dataset generation tool.

Given a hardware profile, produce the optimal model assignment for each pipeline agent.

## OS + Hardware Engine Selection Rules (MANDATORY):

Windows + Copilot+/NPU (Qualcomm Snapdragon X, Intel Core Ultra, AMD Ryzen AI):
  → engine: "onnx" (LiteRT-LM / OpenVINO / DirectML)
  → models: Gemma 3 MatFormer variants, LFM ONNX exports, SLMs via DirectML

Windows + Dedicated GPU (NVIDIA RTX / AMD Radeon):
  → engine: "ollama" (CUDA/ROCm auto-detected)
  → models: standard GGUF quantized via Ollama, all families supported

Windows + Integrated GPU / CPU-only:
  → engine: "llama_cpp" (AVX2/AVX-512 GGUF execution)
  → models: aggressively quantized GGUF (INT4 or lower)

macOS + Apple Silicon (M1–M4 any variant):
  → engine: "mlx" (unified memory, highest throughput on Apple)
  → models: mlx-community/ HuggingFace checkpoints
  → note: use RAM as effective VRAM for sizing decisions

Linux + Dedicated GPU:
  → engine: "ollama" (CUDA/ROCm)

Linux + CPU-only:
  → engine: "llama_cpp"

## Quantization Tiers (apply per hardware capability):

INT8 / Q8   — baseline standard. Use when VRAM/RAM allows (>= 16GB effective)
INT4 / Q4   — budget standard. Default for constrained hardware (6–16GB effective)
INT3 / Q3   — low resource only (3–6GB effective)
INT2        — ultra-low only (<3GB effective). Use sparingly.

## Warm Model Efficiency (IMPORTANT):
Minimise distinct warm model instances. Where two agents share similar size/role:
- Verifier + Fixer: always assign the same tiny model family on low/mid hardware
- Analyser: can share model with Verifier (runs sequentially, not concurrently)
- Sequential task multi-tenancy: same model instance handles multiple lightweight roles

## Agent Requirements:

ORCHESTRATOR
  Cloud: Claude Sonnet 4.6 (preferred), GPT-4.1, Gemini 2.5 Pro
  Local: Gemma 3 27B (high-tier), Gemma 3 12B (mid), Qwen 2.5 7B (low)
  MoE preferred for local (efficient). Cloud preferred overall.

INTERPRETER
  Cloud: Gemini 2.5 Flash (fast JSON), Step 3.5 Flash, Qwen 3
  Local: Qwen 2.5 7B Q4, Gemma 3 4B Q4 (minimum)
  Mode: auto (cloud if no capable local)

ANALYSER
  Local ONLY — cloud too costly for file processing
  Best: Gemma 3 4B Q4, LFM 4B, Qwen 2.5 3B Q4
  NEVER assign cloud_model. mode must be "local".

RESEARCHER
  Cloud ONLY — Gemini (search grounding required)
  cloud_model: "gemini/gemini-2.5-pro" always
  NEVER assign local_model. mode must be "cloud".

GENERATOR
  Cloud: Llama 3.3 70B (via DeepInfra/Featherless), Qwen 3 32B
  Local: Gemma 3 12B Q4 (>=8GB VRAM), Qwen 2.5 14B Q4 (>=10GB)
  Mode: auto

SCRIPTER
  Cloud: Claude Haiku 4.5 (fast, cheap), Gemini Flash
  Local: Qwen 2.5 14B Q4 (>=12GB VRAM only, otherwise cloud)
  Mode: cloud preferred (high throughput, many batches)

VERIFIER
  Local ONLY — checks every row, cost-prohibitive on cloud
  PREFER LFM (SSM/Mamba) — no KV cache growth = no OOM on large datasets
  LFM 4B: ~3GB VRAM, no KV cache, constant memory regardless of sequence length
  Fallback: Qwen 2.5 3B Q4 (Transformer — set kv_cache_clear_interval=50)
  NEVER assign cloud_model. mode must be "local".

FIXER
  Local ONLY — tool-native SLM, tiny, fast
  Best: Qwen 2.5 1.5B (tool-native), FunctionGemma 2B
  NEVER assign cloud_model. mode must be "local".

## VRAM requirements (4-bit quantized):
1.5B≈1.2GB | 3B≈2.0GB | 4B≈2.8GB | 7B≈4.5GB | 8B≈5.0GB
12B≈7.5GB | 14B≈9.0GB | 27B≈16GB | 32B≈20GB | 70B≈42GB

## Output (JSON only, no preamble):
{
  "assignments": {
    "orchestrator": {
      "agent_id": "orchestrator",
      "cloud_model": "claude-sonnet-4-6",
      "cloud_provider": "anthropic",
      "local_model": null,
      "local_engine": null,
      "quantization": null,
      "mode": "cloud"
    },
    "interpreter": { ... },
    "analyser": { ... },
    "researcher": { ... },
    "generator": { ... },
    "scripter": { ... },
    "verifier": { ... },
    "fixer": { ... }
  },
  "engine": "ollama",
  "quant_tier": "q4_K_M",
  "warm_model_note": "Brief note on which models share warm instances",
  "reasoning": "Brief explanation of key decisions for this hardware"
}

Valid local_engine: "ollama" | "llama_cpp" | "mlx" | "onnx" | null
Valid mode: "cloud" | "local" | "auto"
"""


# ─── Main entry point ─────────────────────────────────────────────────────────

async def run_config_agent(
    hardware: HardwareProfile,
    api_keys: dict,
    existing_settings: Optional[AppSettings] = None,
) -> ModelConfig:
    """
    Determine optimal model assignments for this hardware.

    First tries calling a cloud LLM with full OS/hardware context.
    Falls back to deterministic _safe_defaults() if no keys or call fails.
    Preserves any explicit user overrides from existing_settings.
    """
    google_key    = api_keys.get("google")
    anthropic_key = api_keys.get("anthropic")

    if not google_key and not anthropic_key:
        log.info("No API keys — using deterministic safe defaults.")
        return _safe_defaults(hardware)

    user_message = _build_user_message(hardware)
    model        = "gemini/gemini-2.5-flash" if google_key else "claude-haiku-4-5"
    provider     = "google" if google_key else "anthropic"

    call_kwargs: dict = {"max_tokens": 3000, "temperature": 0.05}
    if provider == "google" and google_key:
        call_kwargs["api_key"] = google_key
    elif provider == "anthropic" and anthropic_key:
        litellm.anthropic_key = anthropic_key

    log.info(f"Configuration Agent calling {model}.")

    try:
        response = await litellm.acompletion(
            model=model,
            messages=[
                {"role": "system", "content": _CONFIG_SYSTEM},
                {"role": "user",   "content": user_message},
            ],
            **call_kwargs,
        )

        raw_text = response.choices[0].message.content or ""
        config   = _parse_config_response(raw_text, hardware)

        if config:
            log.info("Configuration Agent returned valid config.")
            if existing_settings and existing_settings.agent_models.assignments:
                config = _merge_user_overrides(config, existing_settings.agent_models)
            return config

        log.warning("Configuration Agent returned invalid config — using safe defaults.")
        return _safe_defaults(hardware)

    except Exception as e:
        log.error(f"Configuration Agent failed ({e}) — using safe defaults.")
        return _safe_defaults(hardware)


def _build_user_message(hardware: HardwareProfile) -> str:
    """Build the full hardware context message for the config agent LLM."""
    os_family = _detect_os_family(hardware)
    engine, quant = _select_engine(hardware)

    gpu_desc = (
        f"{hardware.gpu.name} — {hardware.gpu.vram_gb:.1f} GB VRAM ({hardware.gpu.vendor})"
        if hardware.gpu else "No dedicated GPU"
    )
    npu_desc   = hardware.npu_name or "None detected"
    vram       = hardware.gpu.vram_gb if hardware.gpu else 0.0
    effective  = hardware.ram_gb if os_family == "macos_apple" else vram

    engines_installed = []
    if hardware.has_ollama:      engines_installed.append("Ollama")
    if hardware.has_llama_cpp:   engines_installed.append("llama.cpp")
    if hardware.has_mlx:         engines_installed.append("MLX")
    if hardware.has_onnx_runtime:engines_installed.append("ONNX Runtime")

    return f"""## Hardware Profile

OS Family:          {os_family} (raw: {hardware.os})
GPU:                {gpu_desc}
Effective VRAM:     {effective:.1f} GB {"(unified RAM)" if os_family == "macos_apple" else ""}
System RAM:         {hardware.ram_gb:.1f} GB
CPU:                {hardware.cpu_name}
NPU:                {npu_desc}
Hardware Tier:      {hardware.tier}
Engines Installed:  {", ".join(engines_installed) if engines_installed else "None"}

## Pre-computed Recommendation (validate and refine)

Based on the OS+hardware matrix:
  Recommended engine:      {engine}
  Recommended quant tier:  {quant.lstrip(":")}
  Effective VRAM for sizing: {effective:.1f} GB

## Task

Produce the optimal ModelConfig JSON. Follow the engine selection rules strictly.
Apply warm model consolidation on {hardware.tier} tier hardware.
Assign LFM for Verifier if the engine supports it and VRAM >= 3GB.
Use the pre-computed engine and quant tier unless you have a specific reason to deviate.
"""


def _parse_config_response(
    raw: str, hardware: HardwareProfile
) -> Optional[ModelConfig]:
    """Parse, validate, and enforce hard rules on the LLM's JSON response."""
    # Strip markdown fences
    text = raw.strip()
    if text.startswith("```"):
        nl   = text.find("\n")
        text = text[nl + 1:] if nl != -1 else text[3:]
    if text.endswith("```"):
        text = text[:-3].strip()

    # Extract JSON object
    start = text.find("{")
    end   = text.rfind("}")
    if start == -1 or end <= start:
        log.error("Config agent response: no JSON object found.")
        return None

    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError as e:
        log.error(f"Config agent JSON parse error: {e}")
        return None

    raw_assignments = data.get("assignments", {})
    if not raw_assignments:
        log.error("Config agent response: no assignments key.")
        return None

    # Log reasoning for debugging
    for key in ("reasoning", "warm_model_note", "engine"):
        if val := data.get(key):
            log.info(f"Config agent {key}: {val}")

    engine, quant = _select_engine(hardware)
    assignments: dict[AgentID, ModelAssignment] = {}
    required     = set(AgentID)

    for id_str, raw_a in raw_assignments.items():
        try:
            agent_id = AgentID(id_str)
        except ValueError:
            log.warning(f"Unknown agent ID '{id_str}' in config response — skipping.")
            continue

        try:
            assignment = ModelAssignment(
                agent_id=agent_id,
                cloud_model=raw_a.get("cloud_model") or None,
                cloud_provider=raw_a.get("cloud_provider") or None,
                local_model=raw_a.get("local_model") or None,
                local_engine=raw_a.get("local_engine") or None,
                quantization=raw_a.get("quantization") or None,
                mode=InferenceMode(raw_a.get("mode", "auto")),
            )
        except Exception as e:
            log.warning(f"Invalid assignment for {id_str}: {e} — using safe default.")
            continue

        # Validate local model fits the hardware
        if assignment.local_model:
            ok, reason = check_model_compatibility(
                hardware, assignment.local_model, assignment.quantization
            )
            if not ok:
                log.warning(
                    f"{agent_id}: local model '{assignment.local_model}' incompatible "
                    f"({reason}) — clearing."
                )
                assignment.local_model  = None
                assignment.local_engine = None
                if assignment.mode == InferenceMode.LOCAL:
                    assignment.mode = (
                        InferenceMode.CLOUD if assignment.cloud_model
                        else InferenceMode.AUTO
                    )

        # ── Hard rules — override anything the LLM got wrong ──────────────────

        if agent_id == AgentID.ANALYSER:
            # Always local, never cloud
            assignment.cloud_model    = None
            assignment.cloud_provider = None
            assignment.mode           = InferenceMode.LOCAL
            if not assignment.local_model:
                assignment.local_model  = _engine_model("gemma3:4b", engine, quant)
                assignment.local_engine = engine
                log.warning("Analyser: no local model — defaulting to gemma3:4b.")

        elif agent_id == AgentID.RESEARCHER:
            # Always cloud Gemini
            assignment.local_model    = None
            assignment.local_engine   = None
            assignment.mode           = InferenceMode.CLOUD
            if not assignment.cloud_model:
                assignment.cloud_model    = "gemini/gemini-2.5-pro"
                assignment.cloud_provider = "google"

        elif agent_id == AgentID.VERIFIER:
            # Always local, prefer LFM
            assignment.cloud_model    = None
            assignment.cloud_provider = None
            assignment.mode           = InferenceMode.LOCAL
            assignment = _prefer_lfm_for_verifier(assignment, engine, quant, hardware)

        elif agent_id == AgentID.FIXER:
            # Always local, always tiny
            assignment.cloud_model    = None
            assignment.cloud_provider = None
            assignment.mode           = InferenceMode.LOCAL
            if not assignment.local_model:
                assignment.local_model  = _engine_model("qwen2.5:1.5b", engine, quant)
                assignment.local_engine = engine
                log.warning("Fixer: no local model — defaulting to qwen2.5:1.5b.")

        assignments[agent_id] = assignment

    # Fill missing agents from safe defaults
    safe = _safe_defaults(hardware)
    for agent_id in required:
        if agent_id not in assignments:
            log.warning(f"Config agent missing {agent_id} — using safe default.")
            assignments[agent_id] = safe.assignments[agent_id]

    # Apply warm model consolidation
    assignments = _consolidate_models(assignments, engine, quant, hardware)

    # Final safety pass
    for agent_id, assignment in assignments.items():
        if assignment.mode == InferenceMode.LOCAL and not assignment.local_model:
            if assignment.cloud_model:
                assignment.mode = InferenceMode.CLOUD
            else:
                assignment.cloud_model    = "claude-haiku-4-5"
                assignment.cloud_provider = "anthropic"
                assignment.mode           = InferenceMode.CLOUD
                log.warning(f"{agent_id}: emergency fallback to claude-haiku-4-5.")

    return ModelConfig(assignments=assignments)


def _merge_user_overrides(
    generated: ModelConfig,
    user_config: ModelConfig,
) -> ModelConfig:
    """
    Preserve explicit user overrides (non-AUTO mode assignments).
    AUTO = "let the system decide" → don't preserve.
    """
    merged = dict(generated.assignments)
    for agent_id, user_a in user_config.assignments.items():
        if user_a.mode != InferenceMode.AUTO:
            log.debug(f"Preserving user override for {agent_id}: mode={user_a.mode}")
            merged[agent_id] = user_a
    return ModelConfig(assignments=merged)
