"""
core/litellm_router.py

Unified LLM routing for all agents.
All calls go through here — no agent touches an LLM directly.

Optimizations applied:
  1. Prompt caching   — Anthropic/Groq headers; Google is implicit
  2. Token tracking   — records usage from every response for cost visibility
  3. Context budgeting — enforces per-model context limits via trim_to_budget
  4. Speculative decoding — injects draft_model for eligible Ollama calls
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, AsyncIterator, Dict, List, Optional

import litellm
from litellm import acompletion, ModelResponse
from litellm.exceptions import (
    AuthenticationError,
    BadRequestError,
    RateLimitError,
    ServiceUnavailableError,
)

from core.models import (
    AgentID,
    AppSettings,
    HardwareProfile,
    InferenceMode,
    ModelAssignment,
)
from core.optimizations import (
    TokenTracker,
    build_anthropic_cached_system,
    build_ollama_speculative_extra_body,
    should_cache,
    trim_to_budget,
)

log = logging.getLogger(__name__)

litellm.suppress_debug_info = True
litellm.set_verbose = False


class LiteLLMRouter:
    """Central router for all LLM calls in Datasetter."""

    def __init__(
        self,
        settings: AppSettings,
        hardware: Optional[HardwareProfile] = None,
        token_tracker: Optional[TokenTracker] = None,
    ):
        self.settings      = settings
        self.hardware      = hardware
        self.api_keys      = settings.api_keys
        self.token_tracker = token_tracker or TokenTracker()
        self._configure_litellm()

    def _configure_litellm(self) -> None:
        k = self.api_keys
        if k.anthropic:  litellm.anthropic_key  = k.anthropic
        if k.google:     litellm.gemini_key      = k.google
        if k.deepinfra:  litellm.deepinfra_key   = k.deepinfra
        if k.groq:       litellm.groq_key        = k.groq
        if k.sambanova:  litellm.sambanova_key   = k.sambanova
        if k.fireworks:  litellm.fireworks_key   = k.fireworks

        for ep in self.api_keys.custom_endpoints:
            if not ep.get("url") or not ep.get("label"):
                continue
            try:
                litellm.register_model({
                    ep["label"]: {
                        "litellm_params": {
                            "model":   f"openai/{ep.get('model', 'default')}",
                            "api_base": ep["url"],
                            "api_key":  ep.get("api_key") or "none",
                        }
                    }
                })
            except Exception as e:
                log.warning(f"Could not register custom endpoint '{ep.get('label')}': {e}")

    # ── Assignment resolution ─────────────────────────────────────────────────

    def _resolve_assignment(
        self, agent_id: AgentID, config_override: Optional[ModelAssignment] = None
    ) -> ModelAssignment:
        if config_override:
            return config_override
        assignments = self.settings.agent_models.assignments
        if agent_id in assignments:
            return assignments[agent_id]
        return self._default_assignment(agent_id)

    def _default_assignment(self, agent_id: AgentID) -> ModelAssignment:
        defaults: Dict[AgentID, ModelAssignment] = {
            AgentID.ORCHESTRATOR: ModelAssignment(
                agent_id=agent_id, cloud_model="claude-sonnet-4-6",
                cloud_provider="anthropic", mode=InferenceMode.CLOUD,
            ),
            AgentID.INTERPRETER: ModelAssignment(
                agent_id=agent_id, cloud_model="gemini/gemini-2.5-flash",
                cloud_provider="google", local_model="lfm-2-9ba1b",
                local_engine="ollama", mode=InferenceMode.AUTO,
            ),
            AgentID.ANALYSER: ModelAssignment(
                agent_id=agent_id, local_model="gemma3:4b",
                local_engine="ollama", mode=InferenceMode.LOCAL,
            ),
            AgentID.RESEARCHER: ModelAssignment(
                agent_id=agent_id, cloud_model="gemini/gemini-2.5-pro",
                cloud_provider="google", mode=InferenceMode.CLOUD,
            ),
            AgentID.GENERATOR: ModelAssignment(
                agent_id=agent_id,
                cloud_model="deepinfra/meta-llama/Llama-3.3-70B-Instruct",
                cloud_provider="deepinfra", local_model="gemma3:12b-q4_K_M",
                local_engine="ollama", mode=InferenceMode.AUTO,
            ),
            AgentID.SCRIPTER: ModelAssignment(
                agent_id=agent_id, cloud_model="claude-haiku-4-5",
                cloud_provider="anthropic", mode=InferenceMode.CLOUD,
            ),
            AgentID.VERIFIER: ModelAssignment(
                agent_id=agent_id, local_model="lfm:4b",
                local_engine="ollama", mode=InferenceMode.LOCAL,
            ),
            AgentID.FIXER: ModelAssignment(
                agent_id=agent_id, local_model="qwen2.5:1.5b",
                local_engine="ollama", mode=InferenceMode.LOCAL,
            ),
        }
        return defaults.get(
            agent_id,
            ModelAssignment(
                agent_id=agent_id, cloud_model="claude-haiku-4-5",
                cloud_provider="anthropic", mode=InferenceMode.CLOUD,
            ),
        )

    def _pick_mode(self, assignment: ModelAssignment) -> InferenceMode:
        if assignment.mode != InferenceMode.AUTO:
            return assignment.mode
        has_local = bool(assignment.local_model)
        has_cloud = bool(assignment.cloud_model)
        has_gpu   = bool(
            self.hardware and self.hardware.gpu and self.hardware.gpu.vram_gb >= 4
        )
        if has_local and has_gpu:
            return InferenceMode.LOCAL
        if has_cloud:
            return InferenceMode.CLOUD
        if has_local:
            return InferenceMode.LOCAL
        raise RuntimeError(
            f"No valid inference mode for agent {assignment.agent_id}. "
            "Configure cloud_model or local_model."
        )

    def _build_model_string(
        self, assignment: ModelAssignment, mode: InferenceMode
    ) -> tuple[str, dict]:
        """Return (litellm_model_string, extra_kwargs)."""
        extra: dict = {}

        if mode == InferenceMode.CLOUD:
            model    = assignment.cloud_model
            if not model:
                raise RuntimeError(
                    f"Agent {assignment.agent_id}: cloud mode but cloud_model not set."
                )
            provider = assignment.cloud_provider or ""

            if provider == "featherless":
                extra["api_key"]  = self.api_keys.featherless or "none"
                extra["api_base"] = "https://api.featherless.ai/v1"
                model = f"openai/{model}"
            elif provider == "novita":
                extra["api_key"]  = self.api_keys.novita or "none"
                extra["api_base"] = "https://api.novita.ai/v3/openai"
                model = f"openai/{model}"
            elif provider == "siliconflow":
                extra["api_key"]  = self.api_keys.siliconflow or "none"
                extra["api_base"] = "https://api.siliconflow.cn/v1"
                model = f"openai/{model}"
            elif provider == "runpod":
                extra["api_key"]  = self.api_keys.runpod or "none"
                extra["api_base"] = "https://api.runpod.ai/v2"
                model = f"openai/{model}"
            elif provider == "pollinations":
                extra["api_base"] = "https://text.pollinations.ai/openai"
                extra["api_key"]  = "none"
                model = f"openai/{model}"

            return model, extra

        else:  # LOCAL
            model  = assignment.local_model
            if not model:
                raise RuntimeError(
                    f"Agent {assignment.agent_id}: local mode but local_model not set."
                )
            engine = assignment.local_engine or "ollama"

            if engine in ("ollama", "ollama_chat"):
                # Inject speculative decoding if a draft model exists for this target
                spec = build_ollama_speculative_extra_body(model)
                if spec:
                    extra["extra_body"] = spec
                return f"ollama/{model}", extra
            elif engine in ("llama_cpp", "mlx", "onnx"):
                extra["api_base"] = "http://localhost:8080/v1"
                extra["api_key"]  = "none"
                return f"openai/{model}", extra
            else:
                log.warning(
                    f"Unknown local engine '{engine}' for {assignment.agent_id}. "
                    "Falling back to ollama."
                )
                return f"ollama/{model}", extra

    # ── JSON helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _strip_json_fences(text: str) -> str:
        text = text.strip()
        if text.startswith("```"):
            first_nl = text.find("\n")
            text = text[first_nl + 1:] if first_nl != -1 else text[3:]
        if text.endswith("```"):
            text = text[:-3]
        return text.strip()

    @staticmethod
    def _extract_json_object(text: str) -> str:
        for open_ch, close_ch in [('{', '}'), ('[', ']')]:
            start = text.find(open_ch)
            if start != -1:
                end = text.rfind(close_ch)
                if end > start:
                    return text[start:end + 1]
        return text

    # ── Prompt caching helpers ────────────────────────────────────────────────

    def _apply_caching(
        self,
        system: Optional[str],
        messages: List[Dict[str, Any]],
        provider: str,
        model_str: str,
    ) -> tuple[Optional[Any], List[Dict[str, Any]], Dict[str, str]]:
        """
        Apply prompt caching if appropriate for this provider and prompt size.

        Returns:
            (system_for_call, messages_for_call, extra_headers)

        For Anthropic: system becomes a list of content blocks with cache_control.
        For Groq:      extra_headers includes x-groq-cache.
        For Google:    no changes (caching is implicit).
        """
        if not system or not should_cache(provider, len(system), model_str):
            return system, messages, {}

        extra_headers: Dict[str, str] = {}

        if provider == "anthropic":
            # Convert system string to cached content block list
            cached_system = build_anthropic_cached_system(system)
            extra_headers["anthropic-beta"] = "prompt-caching-2024-07-31"
            return cached_system, messages, extra_headers

        elif provider == "groq":
            extra_headers["x-groq-cache"] = "true"
            return system, messages, extra_headers

        # Google: implicit — no changes
        return system, messages, extra_headers

    # ── Fallback helper ───────────────────────────────────────────────────────

    def _try_fallback(
        self,
        assignment: ModelAssignment,
        current_mode: InferenceMode,
        already_tried: bool,
    ) -> Optional[tuple[InferenceMode, str, dict]]:
        if already_tried or assignment.mode != InferenceMode.AUTO:
            return None
        fallback_mode = (
            InferenceMode.LOCAL if current_mode == InferenceMode.CLOUD
            else InferenceMode.CLOUD
        )
        can_fallback = (
            (fallback_mode == InferenceMode.LOCAL  and assignment.local_model) or
            (fallback_mode == InferenceMode.CLOUD and assignment.cloud_model)
        )
        if not can_fallback:
            return None
        try:
            model_str, extra_kwargs = self._build_model_string(assignment, fallback_mode)
            log.warning(f"Falling back from {current_mode} → {fallback_mode}: {model_str}")
            return fallback_mode, model_str, extra_kwargs
        except Exception as e:
            log.error(f"Fallback build failed: {e}")
            return None

    # ── Main completion methods ───────────────────────────────────────────────

    async def complete(
        self,
        agent_id: AgentID,
        messages: List[Dict[str, Any]],
        system: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        json_mode: bool = False,
        tools: Optional[List[Dict]] = None,
        override: Optional[ModelAssignment] = None,
        retries: int = 3,
        retry_delay: float = 2.0,
        # Optimization controls
        use_cache: bool = True,
        trim_content: bool = True,
    ) -> tuple[str, str]:
        """
        Complete a prompt for the given agent.

        Args:
            use_cache:    Apply prompt caching headers where supported.
            trim_content: Trim message content to fit context window.

        Returns:
            (response_text, model_used_string)
        """
        assignment = self._resolve_assignment(agent_id, override)
        mode       = self._pick_mode(assignment)
        model_str, extra_kwargs = self._build_model_string(assignment, mode)
        provider   = assignment.cloud_provider or "" if mode == InferenceMode.CLOUD else "local"

        # ── Context trimming ──────────────────────────────────────────────────
        if trim_content and system:
            system_budget = len(system)
            # Trim the last user message if it's too large
            if messages:
                last = messages[-1]
                if isinstance(last.get("content"), str):
                    trimmed_content = trim_to_budget(
                        content=last["content"],
                        model_str=model_str,
                        system_chars=system_budget,
                        messages_chars=sum(
                            len(str(m.get("content", ""))) for m in messages[:-1]
                        ),
                        label=f"{agent_id} user message",
                    )
                    if trimmed_content != last["content"]:
                        messages = list(messages)
                        messages[-1] = {**last, "content": trimmed_content}

        # ── Prompt caching ────────────────────────────────────────────────────
        call_system = system
        call_messages = messages
        cache_headers: Dict[str, str] = {}
        if use_cache and mode == InferenceMode.CLOUD:
            call_system, call_messages, cache_headers = self._apply_caching(
                system=system,
                messages=messages,
                provider=provider,
                model_str=model_str,
            )

        # ── Build call kwargs ─────────────────────────────────────────────────
        full_messages: List[Dict[str, Any]] = []
        if call_system:
            # Both plain strings and Anthropic cache-control block lists are
            # passed as a role:system message — litellm handles both forms.
            full_messages.append({"role": "system", "content": call_system})
        full_messages.extend(call_messages)

        call_kwargs: Dict[str, Any] = {
            "model":       model_str,
            "messages":    full_messages,
            "temperature": temperature,
            "max_tokens":  max_tokens,
            **extra_kwargs,
        }

        if json_mode and mode == InferenceMode.CLOUD:
            call_kwargs["response_format"] = {"type": "json_object"}

        if tools:
            call_kwargs["tools"]       = tools
            call_kwargs["tool_choice"] = "auto"

        if cache_headers:
            call_kwargs["extra_headers"] = {
                **call_kwargs.get("extra_headers", {}),
                **cache_headers,
            }

        # ── Retry loop ────────────────────────────────────────────────────────
        last_error:    Optional[Exception] = None
        tried_fallback = False

        for attempt in range(retries):
            try:
                log.debug(f"[{agent_id}] attempt {attempt+1}/{retries} → {model_str}")
                t0       = time.monotonic()
                response: ModelResponse = await acompletion(**call_kwargs)
                elapsed  = time.monotonic() - t0

                # Record token usage
                self.token_tracker.record_from_response(agent_id.value, response)

                content = response.choices[0].message.content or ""
                log.debug(f"[{agent_id}] {elapsed:.2f}s · {len(content)} chars")
                return content, model_str

            except RateLimitError as e:
                last_error = e
                wait = retry_delay * (2 ** attempt)
                log.warning(f"[{agent_id}] rate limited — waiting {wait:.1f}s")
                await asyncio.sleep(wait)

            except AuthenticationError as e:
                last_error = e
                log.error(f"[{agent_id}] auth error: {e}")
                fallback = self._try_fallback(assignment, mode, tried_fallback)
                if fallback:
                    tried_fallback = True
                    mode, model_str, extra_kwargs = fallback
                    provider = ""
                    call_kwargs.update({"model": model_str, **extra_kwargs})
                    call_kwargs.pop("response_format", None)
                    call_kwargs.pop("extra_headers", None)
                    continue
                raise RuntimeError(f"Agent {agent_id} authentication failed: {e}") from e

            except (ServiceUnavailableError, BadRequestError) as e:
                last_error = e
                log.warning(f"[{agent_id}] {type(e).__name__}: {e}")
                fallback = self._try_fallback(assignment, mode, tried_fallback)
                if fallback:
                    tried_fallback = True
                    mode, model_str, extra_kwargs = fallback
                    provider = ""
                    call_kwargs.update({"model": model_str, **extra_kwargs})
                    call_kwargs.pop("response_format", None)
                    call_kwargs.pop("extra_headers", None)
                    continue
                await asyncio.sleep(retry_delay * (attempt + 1))

            except Exception as e:
                last_error = e
                log.error(
                    f"[{agent_id}] unexpected error (attempt {attempt+1}): "
                    f"{type(e).__name__}: {e}"
                )
                await asyncio.sleep(retry_delay * (attempt + 1))

        raise RuntimeError(
            f"Agent {agent_id} exhausted {retries} retries. Last error: {last_error}"
        )

    async def complete_json(
        self,
        agent_id: AgentID,
        messages: List[Dict[str, Any]],
        system: Optional[str] = None,
        temperature: float = 0.4,
        max_tokens: int = 4096,
        override: Optional[ModelAssignment] = None,
        retries: int = 3,
        use_cache: bool = True,
    ) -> tuple[dict | list, str]:
        """Complete and robustly parse a JSON response."""
        last_parse_error: Optional[Exception] = None
        model_used = ""

        for attempt in range(retries):
            use_messages = list(messages)
            if attempt > 0 and last_parse_error:
                correction = (
                    f" IMPORTANT: Your previous response could not be parsed as JSON "
                    f"(error: {last_parse_error}). "
                    f"Respond with ONLY a valid JSON object or array. "
                    f"No markdown, no explanation, no preamble."
                )
                if use_messages and use_messages[-1]["role"] == "user":
                    use_messages[-1] = {
                        "role":    "user",
                        "content": use_messages[-1]["content"] + correction,
                    }
                else:
                    use_messages.append({"role": "user", "content": "Output valid JSON only." + correction})

            try:
                text, model_used = await self.complete(
                    agent_id=agent_id,
                    messages=use_messages,
                    system=system,
                    temperature=max(0.1, temperature - attempt * 0.1),
                    max_tokens=max_tokens,
                    json_mode=True,
                    override=override,
                    retries=2,
                    use_cache=use_cache,
                )
            except RuntimeError:
                raise

            cleaned = self._strip_json_fences(text)
            cleaned = self._extract_json_object(cleaned)

            try:
                return json.loads(cleaned), model_used
            except json.JSONDecodeError as e:
                last_parse_error = e
                log.warning(
                    f"[{agent_id}] JSON parse failed (attempt {attempt+1}/{retries}): {e}. "
                    f"Raw: {text[:150]!r}"
                )

        raise ValueError(
            f"Agent {agent_id} returned invalid JSON after {retries} attempts. "
            f"Last parse error: {last_parse_error}"
        )

    async def complete_tools(
        self,
        agent_id: AgentID,
        messages: List[Dict[str, Any]],
        tools: List[Dict],
        system: Optional[str] = None,
        temperature: float = 0.1,
        max_tokens: int = 2048,
        override: Optional[ModelAssignment] = None,
        retries: int = 3,
        retry_delay: float = 2.0,
    ) -> tuple[List[Dict], str]:
        """Complete with tool use. Full retry with exponential backoff."""
        assignment = self._resolve_assignment(agent_id, override)
        mode       = self._pick_mode(assignment)
        model_str, extra_kwargs = self._build_model_string(assignment, mode)

        full_messages: List[Dict[str, Any]] = []
        if system:
            full_messages.append({"role": "system", "content": system})
        full_messages.extend(messages)

        last_error: Optional[Exception] = None

        for attempt in range(retries):
            try:
                response: ModelResponse = await acompletion(
                    model=model_str,
                    messages=full_messages,
                    tools=tools,
                    tool_choice="auto",
                    temperature=temperature,
                    max_tokens=max_tokens,
                    **extra_kwargs,
                )

                self.token_tracker.record_from_response(agent_id.value, response)

                message    = response.choices[0].message
                tool_calls: List[Dict] = []

                if hasattr(message, "tool_calls") and message.tool_calls:
                    for tc in message.tool_calls:
                        try:
                            tool_calls.append({
                                "name":      tc.function.name,
                                "arguments": json.loads(tc.function.arguments),
                            })
                        except (json.JSONDecodeError, AttributeError) as parse_err:
                            log.warning(f"[{agent_id}] could not parse tool args: {parse_err}")

                return tool_calls, model_str

            except RateLimitError as e:
                last_error = e
                wait = retry_delay * (2 ** attempt)
                log.warning(f"[{agent_id}] tool rate limited — waiting {wait:.1f}s")
                await asyncio.sleep(wait)

            except Exception as e:
                last_error = e
                log.error(
                    f"[{agent_id}] tool error (attempt {attempt+1}/{retries}): "
                    f"{type(e).__name__}: {e}"
                )
                await asyncio.sleep(retry_delay * (attempt + 1))

        raise RuntimeError(
            f"Agent {agent_id} tool call failed after {retries} retries. Last: {last_error}"
        )

    async def stream(
        self,
        agent_id: AgentID,
        messages: List[Dict[str, Any]],
        system: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        override: Optional[ModelAssignment] = None,
    ) -> AsyncIterator[str]:
        """Stream tokens. Handles mid-stream errors gracefully."""
        assignment = self._resolve_assignment(agent_id, override)
        mode       = self._pick_mode(assignment)
        model_str, extra_kwargs = self._build_model_string(assignment, mode)

        full_messages: List[Dict[str, Any]] = []
        if system:
            full_messages.append({"role": "system", "content": system})
        full_messages.extend(messages)

        try:
            response = await acompletion(
                model=model_str,
                messages=full_messages,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=True,
                **extra_kwargs,
            )

            async for chunk in response:
                try:
                    delta = chunk.choices[0].delta
                    if delta and delta.content:
                        yield delta.content
                except (IndexError, AttributeError):
                    continue

        except Exception as e:
            log.error(f"[{agent_id}] stream error: {type(e).__name__}: {e}")
            raise RuntimeError(f"Agent {agent_id} stream failed: {e}") from e
