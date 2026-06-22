"""
core/optimizations.py

Inference optimizations for Datasetter.
All optimizations are opt-in and degrade gracefully if not supported.

1. PROMPT CACHING
   Anthropic (claude-*), Google (gemini-*), and Groq support prefix caching.
   System prompts and blueprint text that are identical across many calls
   get cached after the first call — subsequent calls pay only for the
   non-cached (completion) tokens.

   Anthropic: send cache_control breakpoints in message content blocks.
   Google:    automatic implicit caching — no special headers needed.
   Groq:      pass extra_headers={"x-groq-cache": "true"}.

2. TOKEN BUDGET MANAGEMENT
   Each model has a known context window. We track estimated token usage
   per call and truncate content that would exceed the window.
   Rough heuristic: 1 token ≈ 4 chars (English). Adjust per language.

3. CONTEXT TRIMMING
   The blueprint is a long document. Most of it is irrelevant for a given
   category. We extract only the category-relevant section before injecting
   into prompts, saving 60-80% of blueprint tokens per call.

4. SPECULATIVE DECODING (Ollama)
   Ollama supports speculative decoding via the `options.draft_model` field.
   When a draft model is configured for a target model, Ollama uses it to
   pre-generate candidate tokens that the target model accepts or rejects.
   Typically 2-4x throughput improvement on CPU-bottlenecked inference.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

# ─── Known model context windows (chars, not tokens — rough 4 chars/token) ───

_MODEL_CONTEXT_CHARS: Dict[str, int] = {
    # Anthropic
    "claude-sonnet-4-6":    200_000 * 4,
    "claude-haiku-4-5":     200_000 * 4,
    "claude-opus-4-6":      200_000 * 4,
    # Google
    "gemini-2.5-pro":       1_048_576 * 4,
    "gemini-2.5-flash":     1_048_576 * 4,
    "gemini-2.0-flash":       32_768 * 4,
    # Groq
    "llama-3.3-70b-versatile": 128_000 * 4,
    # Local — conservative estimates
    "gemma3:4b":             8_192 * 4,
    "gemma3:12b":           16_384 * 4,
    "gemma3:27b":           16_384 * 4,
    "gemma3:4b-q4_K_M":     8_192 * 4,
    "gemma3:12b-q4_K_M":   16_384 * 4,
    "qwen2.5:1.5b":         32_768 * 4,
    "qwen2.5:3b-q4_K_M":   32_768 * 4,
    "qwen2.5:7b-q4_K_M":   32_768 * 4,
    "qwen2.5:14b-q4_K_M":  32_768 * 4,
    "lfm:4b":               32_768 * 4,
    "lfm-2-9ba1b":          32_768 * 4,
}

# Default for unknown models — conservative 8K chars
_DEFAULT_CONTEXT_CHARS = 8_192 * 4

# Reserve this fraction for the model's output
_OUTPUT_RESERVE = 0.35


def get_context_limit(model_str: str) -> int:
    """
    Return the estimated context window in characters for a model.
    Strips provider prefixes (ollama/, openai/, gemini/) before lookup.
    """
    # Strip provider prefix
    base = model_str.split("/")[-1].lower()

    # Direct match
    if base in _MODEL_CONTEXT_CHARS:
        return _MODEL_CONTEXT_CHARS[base]

    # Prefix match (handles versioned names like gemini-2.5-flash-001)
    for key, limit in _MODEL_CONTEXT_CHARS.items():
        if base.startswith(key) or key.startswith(base.split(":")[0]):
            return limit

    log.debug(f"Unknown model context for '{model_str}' — using default {_DEFAULT_CONTEXT_CHARS} chars")
    return _DEFAULT_CONTEXT_CHARS


def trim_to_budget(
    content: str,
    model_str: str,
    system_chars: int = 0,
    messages_chars: int = 0,
    label: str = "content",
) -> str:
    """
    Trim `content` so the total prompt fits within the model's context window.
    Preserves the beginning and end of the content (most important sections)
    with a middle truncation marker.

    Args:
        content:        The text to potentially trim.
        model_str:      The model being called — determines context limit.
        system_chars:   Characters already used by the system prompt.
        messages_chars: Characters already used by other message content.
        label:          Log label for what was trimmed.

    Returns:
        Possibly-trimmed content string.
    """
    limit      = get_context_limit(model_str)
    input_limit = int(limit * (1 - _OUTPUT_RESERVE))
    overhead   = system_chars + messages_chars
    budget     = input_limit - overhead - 100  # 100 char safety margin

    if budget <= 0:
        log.warning(f"Token budget exhausted before {label} — returning empty.")
        return ""

    if len(content) <= budget:
        return content  # fits as-is

    # Middle truncation: keep first 60% and last 40% of the budget
    keep_head = int(budget * 0.60)
    keep_tail = int(budget * 0.40)
    truncated = (
        content[:keep_head]
        + f"\n\n[... {len(content) - keep_head - keep_tail} chars trimmed to fit context window ...]\n\n"
        + content[-keep_tail:]
    )
    log.debug(
        f"Trimmed {label}: {len(content)} → {len(truncated)} chars "
        f"(model={model_str}, budget={budget})"
    )
    return truncated


# ─── Context trimming — extract relevant blueprint section ────────────────────

def trim_blueprint_for_category(blueprint: str, category: str) -> str:
    """
    Extract the category-relevant section from the blueprint.
    Returns the full blueprint if no category section is found.

    The Generator writes blueprints with per-category sections like:
        ### Category Name
        ... instructions ...

    We extract just that section plus the global header (~first 800 chars).
    This typically reduces blueprint size from 3000 chars to 400-800 chars.
    """
    if not blueprint or not category:
        return blueprint

    lines      = blueprint.splitlines()
    global_end = 0  # end of the global header section
    cat_start  = -1
    cat_end    = len(lines)

    for i, line in enumerate(lines):
        stripped = line.strip()
        # Global section ends at first ### heading
        if stripped.startswith("###") and global_end == 0:
            global_end = i

        # Find this category's section
        if stripped.lower() == f"### {category.lower()}":
            cat_start = i
            continue

        # End of this category's section = next ### heading after cat_start
        if cat_start != -1 and i > cat_start and stripped.startswith("###"):
            cat_end = i
            break

    global_header  = "\n".join(lines[:global_end]) if global_end > 0 else ""
    category_section = "\n".join(lines[cat_start:cat_end]) if cat_start != -1 else ""

    if not category_section:
        # No dedicated section found — return full blueprint
        return blueprint

    trimmed = "\n\n".join(filter(None, [global_header, category_section]))
    log.debug(
        f"Blueprint trimmed for '{category}': {len(blueprint)} → {len(trimmed)} chars"
    )
    return trimmed


# ─── Prompt caching headers ───────────────────────────────────────────────────

def build_anthropic_cached_system(system: str) -> List[Dict[str, Any]]:
    """
    Build an Anthropic system prompt as a list of content blocks with cache_control.
    This format tells Anthropic to cache the system prompt as a prefix.

    LiteLLM passes this through as-is when the model is claude-*.
    """
    return [
        {
            "type": "text",
            "text": system,
            "cache_control": {"type": "ephemeral"},
        }
    ]


def should_cache(provider: str, system_len: int, model_str: str) -> bool:
    """
    Decide whether to apply prompt caching for this call.

    Anthropic: cache if system prompt >= 1024 tokens (~4096 chars)
    Google:    always (automatic, no cost to enable)
    Groq:      always (header-based, no cost to enable)
    Other:     never
    """
    if provider == "anthropic":
        # Anthropic charges for caching writes but saves on reads.
        # Only worth it if the system prompt is substantial.
        return system_len >= 4096
    elif provider in ("google", "groq"):
        return True
    return False


# ─── Speculative decoding ─────────────────────────────────────────────────────
#
# Tiered complexity routing (from the warm model efficiency framework):
#   Simple   → baseline warm model directly
#   Medium   → small draft model speculatively decodes, target model verifies
#   High     → tiny model drafts, genuinely high-level model verifies
#
# Engine support:
#   Ollama    → extra_body: { "draft_model": "...", "options": { "num_draft": N } }
#   llama.cpp → --draft-model CLI flag (set at server launch, not per-call)
#   MLX       → not supported natively yet (as of mlx-lm 0.18)
#   ONNX      → not supported

# Draft model pairings: target → draft (same family, smaller)
_DRAFT_PAIRINGS: Dict[str, str] = {
    # Gemma 3 family
    "gemma3:27b":            "gemma3:4b",
    "gemma3:27b-q4_k_m":    "gemma3:4b",
    "gemma3:27b-q8_0":      "gemma3:4b",
    "gemma3:12b":            "gemma3:4b",
    "gemma3:12b-q4_k_m":    "gemma3:4b",
    "gemma3:12b-q8_0":      "gemma3:4b",
    # Qwen 2.5 family
    "qwen2.5:14b":           "qwen2.5:1.5b",
    "qwen2.5:14b-q4_k_m":   "qwen2.5:1.5b",
    "qwen2.5:7b":            "qwen2.5:1.5b",
    "qwen2.5:7b-q4_k_m":    "qwen2.5:1.5b",
    "qwen2.5:3b":            "qwen2.5:1.5b",
    # Llama 3 family
    "llama3.3:70b":          "llama3.2:3b",
    "llama3.2:3b":           "qwen2.5:1.5b",
    # MLX names (speculative not yet supported but keep pairing table for future)
    "mlx-community/gemma-3-12b-it-4bit": "mlx-community/gemma-3-4b-it-4bit",
    "mlx-community/gemma-3-27b-it-4bit": "mlx-community/gemma-3-4b-it-4bit",
}


def get_draft_model(target_model: str, engine: str = "ollama") -> Optional[str]:
    """
    Return the recommended draft model for speculative decoding.

    Args:
        target_model: The target model name.
        engine:       Inference engine — "ollama" | "llama_cpp" | "mlx" | "onnx"

    Returns:
        Draft model name, or None if not applicable.
    """
    # MLX speculative decoding not yet supported
    if engine in ("mlx", "onnx"):
        return None

    # Normalize: strip provider prefix and lower-case
    base = target_model.split("/")[-1].lower()

    # Direct match
    if base in _DRAFT_PAIRINGS:
        return _DRAFT_PAIRINGS[base]

    # Prefix match — handles versioned suffixes like gemma3:12b-q4_K_M
    for target_key, draft in _DRAFT_PAIRINGS.items():
        if base.startswith(target_key.lower()):
            return draft

    return None


def build_ollama_speculative_extra_body(
    target_model: str,
    draft_model: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Build extra_body for LiteLLM to enable Ollama speculative decoding.
    LiteLLM forwards extra_body to the underlying Ollama API.

    Ollama speculative decoding requires:
      - draft_model: smaller model from same family
      - options.num_draft: how many tokens the draft model generates per step
        (8 is a good default — balances throughput vs verification overhead)

    Returns empty dict if no valid draft model exists for this target.
    """
    draft = draft_model or get_draft_model(target_model, engine="ollama")
    if not draft:
        return {}

    log.debug(f"Speculative decoding: {target_model} ← {draft} (num_draft=8)")
    return {
        "draft_model": draft,
        "options": {
            "num_draft": 8,
        },
    }


def build_llamacpp_speculative_args(
    target_model: str,
    draft_model: Optional[str] = None,
) -> List[str]:
    """
    Build CLI args for llama-server to enable speculative decoding.
    These are passed at server startup, not per-call.

    Returns:
        List of CLI args to append to the llama-server command.
        Empty list if no valid draft model.
    """
    draft = draft_model or get_draft_model(target_model, engine="llama_cpp")
    if not draft:
        return []

    log.debug(f"llama.cpp speculative: {target_model} ← {draft}")
    return [
        "--draft-model", draft,
        "--draft",       "8",      # draft tokens per step
    ]


# ─── Parallel batch utilities ─────────────────────────────────────────────────

def optimal_parallelism(
    n_items: int,
    model_str: str,
    is_local: bool,
    hardware_tier: str = "mid",
    is_lfm: bool = False,
) -> int:
    """
    Determine the optimal number of concurrent LLM calls.

    LFM models (SSM/Mamba — no KV cache):
      Memory usage is constant regardless of concurrency.
      Can safely run multiple concurrent calls even on low VRAM.
      Up to 4 concurrent on local for LFMs.

    Transformer models (local):
      VRAM-bound. One call at a time unless tiny model on high-tier hardware.

    Cloud models:
      Rate-limited by RPM. Conservative concurrency to avoid 429s.

    Args:
        n_items:       Total items to process (caps the return value).
        model_str:     Model name (used to detect LFM and tiny models).
        is_local:      True for Ollama/llama.cpp/mlx inference.
        hardware_tier: "low" | "mid" | "high" | "ultra"
        is_lfm:        True if model is SSM/Mamba (no KV cache).

    Returns:
        Max concurrent calls (semaphore value).
    """
    if is_local:
        if is_lfm:
            # LFMs: constant memory — concurrency is safe
            lfm_concurrency = {
                "low":   2,
                "mid":   3,
                "high":  4,
                "ultra": 4,
            }
            return min(n_items, lfm_concurrency.get(hardware_tier, 2))

        # Transformer local models — VRAM-bound
        model_lower = model_str.lower()
        is_tiny = any(x in model_lower for x in (
            "1.5b", "2b", "3b", "4b", "gemma3:4b", "qwen2.5:1.5b"
        ))

        if hardware_tier == "ultra":
            return min(n_items, 4 if is_tiny else 2)
        elif hardware_tier == "high":
            return min(n_items, 2 if is_tiny else 1)
        else:
            return 1  # low/mid: serialize to prevent VRAM OOM

    # Cloud — rate limit conservative
    tier_concurrency = {
        "low":   2,
        "mid":   4,
        "high":  6,
        "ultra": 8,
    }
    return min(n_items, tier_concurrency.get(hardware_tier, 4))


# ─── Token usage tracker ─────────────────────────────────────────────────────

class TokenTracker:
    """
    Tracks token usage across all agent calls for a job.
    Used for cost estimation and budget enforcement.
    """

    def __init__(self):
        self._usage: Dict[str, Dict[str, int]] = {}
        # { agent_id: { "prompt": N, "completion": N, "cached": N } }

    def record(
        self,
        agent_id: str,
        prompt_tokens: int,
        completion_tokens: int,
        cached_tokens: int = 0,
    ) -> None:
        if agent_id not in self._usage:
            self._usage[agent_id] = {"prompt": 0, "completion": 0, "cached": 0}
        self._usage[agent_id]["prompt"]     += prompt_tokens
        self._usage[agent_id]["completion"] += completion_tokens
        self._usage[agent_id]["cached"]     += cached_tokens

    def record_from_response(self, agent_id: str, response: Any) -> None:
        """Extract and record token usage from a litellm ModelResponse."""
        try:
            usage = response.usage
            if not usage:
                return
            prompt     = getattr(usage, "prompt_tokens",     0) or 0
            completion = getattr(usage, "completion_tokens", 0) or 0
            # Anthropic and Groq report cached tokens in usage details
            cached = 0
            if hasattr(usage, "prompt_tokens_details") and usage.prompt_tokens_details:
                cached = getattr(usage.prompt_tokens_details, "cached_tokens", 0) or 0
            self.record(agent_id, prompt, completion, cached)
        except Exception:
            pass  # Usage tracking is best-effort

    def total(self) -> Dict[str, int]:
        totals = {"prompt": 0, "completion": 0, "cached": 0, "total": 0}
        for usage in self._usage.values():
            totals["prompt"]     += usage["prompt"]
            totals["completion"] += usage["completion"]
            totals["cached"]     += usage["cached"]
        totals["total"] = totals["prompt"] + totals["completion"]
        return totals

    def per_agent(self) -> Dict[str, Dict[str, int]]:
        return dict(self._usage)

    def cache_hit_rate(self) -> float:
        t = self.total()
        if t["prompt"] == 0:
            return 0.0
        return t["cached"] / t["prompt"]

    def summary(self) -> str:
        t = self.total()
        rate = self.cache_hit_rate()
        return (
            f"Tokens — prompt: {t['prompt']:,}  completion: {t['completion']:,}  "
            f"cached: {t['cached']:,} ({rate:.0%} cache hit rate)  "
            f"total: {t['total']:,}"
        )
