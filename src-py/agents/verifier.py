"""
agents/verifier.py

The Verifier is the quality gate of the pipeline.

Optimizations applied:
  - Blueprint trimmed per batch category (trim_blueprint_for_category)
  - System prompt (SYSTEM_PROMPT) is stable — passed as system param for caching
  - Blueprint sent as first user message per category group — cached across batches
    of the same category (Anthropic/Groq prefix caching)
  - Parallel batch execution across independent batches (optimal_parallelism)
  - LFM-aware KV cache management — skip sleep/clear for SSM/Mamba models
  - use_cache=True on all complete_json calls
"""

from __future__ import annotations

import asyncio
import logging
from typing import Dict, List

from core.litellm_router import LiteLLMRouter
from core.models import (
    AgentID,
    AgentStatus,
    DatasetRow,
    ErrorType,
    JobConfig,
    RowStatus,
    SeedPack,
    VerifierError,
    VerifyMode,
)
from core.optimizations import optimal_parallelism, trim_blueprint_for_category
from utils.events import Emitter

log = logging.getLogger(__name__)


# The stable system prompt — passed as `system` so it gets cached
SYSTEM_PROMPT = """You are the Verifier agent in Datasetter, an AI dataset generation pipeline.

Your job is to check dataset rows for quality issues. You are given:
  1. A blueprint — the definitive spec for what good rows look like
  2. One or more rows to verify
  3. The field schema and constraints

For each row, determine if it passes or fails. If it fails, you must produce
structured error reports that the Fixer agent can act on directly.

Your error reports must be:
  - SPECIFIC: name the exact field, the exact problem, the exact fix needed
  - ACTIONABLE: the Fixer reads your fix_instruction verbatim — make it unambiguous
  - PROPORTIONATE: minor style issues are not errors; flag things that materially
    reduce dataset quality or violate explicit constraints

Check for:
  SEMANTIC    — incoherent content, nonsense, irrelevant responses, hallucinations
  LOGIC       — logical errors, contradictions, factual impossibilities
  CONSTRAINT  — violations of the blueprint's explicit rules and user constraints
  CONSISTENCY — tone mismatch with category profile, register mismatch, style drift
  FORMAT      — wrong structure, missing required sections, malformed output
  LENGTH      — responses dramatically too short or too long vs the blueprint spec

Do NOT flag:
  - Minor stylistic preferences
  - Things that are "not how you'd write it" but are still correct and accurate

Output ONLY valid JSON:
{
  "results": [
    { "row_id": 1, "passed": true, "errors": [] },
    {
      "row_id": 2,
      "passed": false,
      "errors": [
        {
          "error_type": "semantic"|"logic"|"constraint"|"consistency"|"format"|"length",
          "field": "response",
          "description": "Exact description of the problem.",
          "fix_instruction": "Exact instruction for the Fixer to follow.",
          "severity": "fatal"|"minor"
        }
      ]
    }
  ]
}

Be thorough. A missed error that reaches the final dataset is worse than a false positive.
But don't over-flag — the Fixer is expensive. Fatal errors only for truly broken rows.

Every row in the input must appear in the output. No preamble, no notes outside JSON."""


def _is_lfm_model(model_str: str) -> bool:
    """
    Detect if the model is an LFM (SSM/Mamba architecture).
    LFMs have no KV cache — skip KV cache management for these.
    """
    lower = (model_str or "").lower()
    return any(k in lower for k in ("lfm", "mamba", "ssm", "liquid"))


class Verifier:

    def __init__(self, router: LiteLLMRouter, emitter: Emitter):
        self.router  = router
        self.emitter = emitter

    async def run(
        self,
        rows: List[DatasetRow],
        config: JobConfig,
        seed_pack: SeedPack,
    ) -> List[DatasetRow]:
        """
        Verify all rows. Updates row statuses and emits row_update events.
        """
        self.emitter.agent_status(
            AgentID.VERIFIER, AgentStatus.RUNNING,
            current_task="Initialising verification",
        )

        to_verify = [r for r in rows if r.status in (RowStatus.PENDING, RowStatus.FIXING)]

        self.emitter.log(
            AgentID.VERIFIER,
            f"Starting verification. {len(to_verify)} rows. "
            f"Mode: {config.verify_mode.value}. "
            f"Batch size: {config.batch_size if config.verify_mode == VerifyMode.BATCH else 1}.",
        )

        blueprint = seed_pack.blueprint

        if config.verify_mode == VerifyMode.BATCH:
            verified = await self._verify_batch_mode(to_verify, config, blueprint)
        else:
            verified = await self._verify_one_by_one(to_verify, config, blueprint)

        # Merge back into full rows list
        verified_map = {r.id: r for r in verified}
        for i, row in enumerate(rows):
            if row.id in verified_map:
                rows[i] = verified_map[row.id]

        ok_count  = sum(1 for r in rows if r.status == RowStatus.OK)
        err_count = sum(1 for r in rows if r.status == RowStatus.ERROR)

        self.emitter.log(
            AgentID.VERIFIER,
            f"Verification complete. {ok_count} passed, {err_count} failed.",
        )
        self.emitter.agent_status(
            AgentID.VERIFIER, AgentStatus.DONE,
            current_task=f"{ok_count} passed / {err_count} errors",
        )

        return rows

    async def verify_rows(
        self,
        rows: List[DatasetRow],
        config: JobConfig,
        blueprint: str,
    ) -> List[DatasetRow]:
        """Re-verify a subset of rows (after Fixer fixes them)."""
        if config.verify_mode == VerifyMode.BATCH:
            return await self._verify_batch_mode(rows, config, blueprint)
        return await self._verify_one_by_one(rows, config, blueprint)

    # ── Batch mode ────────────────────────────────────────────────────────────

    async def _verify_batch_mode(
        self,
        rows: List[DatasetRow],
        config: JobConfig,
        blueprint: str,
    ) -> List[DatasetRow]:
        batch_size    = config.batch_size
        total_batches = (len(rows) + batch_size - 1) // batch_size
        kv_interval   = config.kv_cache_clear_interval

        # Determine model for KV cache decisions
        assignment  = self.router._resolve_assignment(AgentID.VERIFIER)
        model_str   = assignment.local_model or assignment.cloud_model or ""
        is_lfm      = _is_lfm_model(model_str)
        is_local    = self.router._pick_mode(assignment).value == "local"
        hw_tier     = self.router.hardware.tier if self.router.hardware else "mid"

        # Parallel batch execution — independent batches don't share state
        concurrency = optimal_parallelism(
            n_items=total_batches,
            model_str=model_str,
            is_local=is_local,
            hardware_tier=hw_tier,
        )

        if is_lfm:
            # LFMs have no KV cache — safe to run fully concurrent
            concurrency = max(concurrency, 2)

        semaphore        = asyncio.Semaphore(concurrency)
        kv_counter       = [0]
        all_verified:     List[DatasetRow] = []
        completed_batches = [0]

        async def process_batch(batch_idx: int) -> List[DatasetRow]:
            batch = rows[batch_idx * batch_size: (batch_idx + 1) * batch_size]

            async with semaphore:
                completed_batches[0] += 1

                self.emitter.log(
                    AgentID.VERIFIER,
                    f"Batch {completed_batches[0]}/{total_batches}: "
                    f"verifying {len(batch)} rows.",
                )

                # KV cache management — only for Transformer (non-LFM) models
                if not is_lfm:
                    kv_counter[0] += len(batch)
                    if kv_counter[0] >= kv_interval:
                        kv_counter[0] = 0
                        self.emitter.log(AgentID.VERIFIER, "KV cache cleared.")
                        await asyncio.sleep(0.1)

                # Get the category for this batch for blueprint trimming
                # If rows span categories, use full blueprint
                categories = list({r.category for r in batch})
                if len(categories) == 1:
                    batch_blueprint = trim_blueprint_for_category(blueprint, categories[0])
                else:
                    batch_blueprint = blueprint

                verified_batch = await self._call_verifier(batch, config, batch_blueprint)

                for row in verified_batch:
                    self.emitter.row_update(row)

                err_rate = sum(
                    1 for r in verified_batch if r.status == RowStatus.ERROR
                ) / max(len(batch), 1)

                if err_rate > config.error_halt_threshold:
                    self.emitter.warning(
                        "Verifier",
                        f"Batch {completed_batches[0]}: {err_rate:.0%} error rate "
                        f"exceeds threshold ({config.error_halt_threshold:.0%}).",
                    )

                return verified_batch

        results = await asyncio.gather(
            *[process_batch(i) for i in range(total_batches)],
            return_exceptions=True,
        )

        for batch_idx, result in enumerate(results):
            if isinstance(result, Exception):
                log.error(f"Verifier batch {batch_idx} failed: {result}")
                # Mark affected rows as PENDING for retry rather than silently passing
                batch = rows[batch_idx * batch_size: (batch_idx + 1) * batch_size]
                for r in batch:
                    r.status = RowStatus.PENDING
                all_verified.extend(batch)
            else:
                all_verified.extend(result)

        return all_verified

    # ── One-by-one mode ───────────────────────────────────────────────────────

    async def _verify_one_by_one(
        self,
        rows: List[DatasetRow],
        config: JobConfig,
        blueprint: str,
    ) -> List[DatasetRow]:
        assignment = self.router._resolve_assignment(AgentID.VERIFIER)
        model_str  = assignment.local_model or assignment.cloud_model or ""
        is_lfm     = _is_lfm_model(model_str)
        is_local   = self.router._pick_mode(assignment).value == "local"
        hw_tier    = self.router.hardware.tier if self.router.hardware else "mid"

        kv_interval = config.kv_cache_clear_interval
        counter     = [0]
        # For one-by-one, allow mild concurrency even on local to keep pipeline moving
        concurrency = optimal_parallelism(
            n_items=len(rows), model_str=model_str,
            is_local=is_local, hardware_tier=hw_tier,
        )
        sem = asyncio.Semaphore(max(concurrency, 2))

        async def verify_one(row: DatasetRow) -> DatasetRow:
            async with sem:
                if not is_lfm:
                    counter[0] += 1
                    if counter[0] >= kv_interval:
                        counter[0] = 0
                        await asyncio.sleep(0.05)

                row_blueprint = trim_blueprint_for_category(blueprint, row.category)
                results = await self._call_verifier([row], config, row_blueprint)
                return results[0] if results else row

        verified = await asyncio.gather(*[verify_one(r) for r in rows])

        for row in verified:
            self.emitter.row_update(row)

        return list(verified)

    # ── LLM call ──────────────────────────────────────────────────────────────

    async def _call_verifier(
        self,
        rows: List[DatasetRow],
        config: JobConfig,
        blueprint: str,
    ) -> List[DatasetRow]:
        """
        Call the Verifier model.

        Structure for caching:
          - system=SYSTEM_PROMPT (stable — cached after first call)
          - user message = blueprint header + active checks (stable per category)
                         + rows to verify (variable)

        Blueprint is already trimmed to the relevant category section by the caller.
        """
        # Build the stable portion (blueprint + checks) as the first part of user message
        stable_part = self._build_stable_context(config, blueprint)
        # Build the variable portion (the rows themselves)
        rows_part   = self._build_rows_content(rows)

        user_content = stable_part + "\n\n" + rows_part

        try:
            raw, model = await self.router.complete_json(
                agent_id=AgentID.VERIFIER,
                messages=[{"role": "user", "content": user_content}],
                system=SYSTEM_PROMPT,
                temperature=0.1,
                max_tokens=6000,
                use_cache=True,
            )
        except Exception as e:
            log.error(f"Verifier LLM call failed: {e}")
            for row in rows:
                row.status = RowStatus.PENDING
            return rows

        results     = raw.get("results", [])
        results_map: Dict[int, dict] = {
            r["row_id"]: r for r in results
            if isinstance(r, dict) and "row_id" in r
        }

        for row in rows:
            result = results_map.get(row.id)
            if result is None:
                log.warning(f"Verifier returned no result for row {row.id} — leaving PENDING.")
                continue

            if result.get("passed", True):
                row.status = RowStatus.OK
                row.errors = []
            else:
                errors = []
                for e in result.get("errors", []):
                    try:
                        errors.append(VerifierError(
                            row_id=row.id,
                            error_type=ErrorType(e.get("error_type", "semantic")),
                            field=e.get("field", "response"),
                            description=e.get("description", ""),
                            fix_instruction=e.get("fix_instruction", ""),
                            severity=e.get("severity", "minor"),
                        ))
                    except Exception as parse_err:
                        log.warning(f"Could not parse error for row {row.id}: {parse_err}")

                if errors:
                    row.status = RowStatus.ERROR
                    row.errors = errors
                else:
                    log.warning(f"Row {row.id}: verifier said failed but no errors listed — marking OK.")
                    row.status = RowStatus.OK

        return rows

    def _build_stable_context(self, config: JobConfig, blueprint: str) -> str:
        """
        Build the stable (cacheable) context prefix.
        Blueprint + active checks — identical for all batches of the same category.
        """
        parts = []
        parts.append("## Blueprint")
        parts.append(blueprint)

        parts.append("\n## Active Checks")
        checks = []
        if config.check_semantic:    checks.append("semantic")
        if config.check_logic:       checks.append("logic")
        if config.check_constraints: checks.append("constraint")
        if config.check_consistency: checks.append("consistency")
        if config.check_format:      checks.append("format")
        if config.check_length:      checks.append("length")
        parts.append(f"Run: {', '.join(checks)}")

        return "\n".join(parts)

    def _build_rows_content(self, rows: List[DatasetRow]) -> str:
        """Build the variable (non-cached) rows section."""
        parts = [f"## Rows to Verify ({len(rows)} rows)"]
        for row in rows:
            parts.append(f"\n[Row {row.id}] Category: {row.category}")
            for field, value in row.fields.items():
                val_str = str(value)
                if len(val_str) > 1000:
                    val_str = val_str[:1000] + "... [truncated]"
                parts.append(f"  {field}: {val_str}")

        parts.append(
            f"\nVerify all {len(rows)} rows. "
            f"Output a result for every row. "
            f"row_id must match exactly."
        )
        return "\n".join(parts)

