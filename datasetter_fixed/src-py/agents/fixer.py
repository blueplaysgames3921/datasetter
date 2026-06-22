"""
agents/fixer.py

The Fixer is the simplest agent in the pipeline — intentionally so.
It receives structured VerifierError objects and executes targeted fixes.

Key properties:
  - Uses a tool-native SLM (Qwen2.5 1.5B / FunctionGemma)
  - Does NOT reason about the dataset, the prompt, or quality standards
  - Does NOT re-read the blueprint or constraints
  - Receives one error at a time with an explicit fix_instruction
  - Executes the instruction and returns the fixed field value
  - Fast, cheap, surgical

The Verifier does all the thinking.
The Fixer just does what it's told.

If a row fails re-verification after max_fix_rounds, it's surfaced
to the user for manual review.
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
    JobConfig,
    RowStatus,
    SeedPack,
    VerifierError,
)
from utils.events import Emitter
from utils.storage import update_row

log = logging.getLogger(__name__)


# ── Tool definition for the SLM ───────────────────────────────────────────────

FIX_TOOL = {
    "type": "function",
    "function": {
        "name": "apply_fix",
        "description": "Apply a fix to a specific field in a dataset row.",
        "parameters": {
            "type": "object",
            "properties": {
                "field": {
                    "type": "string",
                    "description": "The name of the field to fix."
                },
                "fixed_value": {
                    "type": "string",
                    "description": "The corrected value for the field."
                }
            },
            "required": ["field", "fixed_value"]
        }
    }
}

SYSTEM_PROMPT = """You are the Fixer agent in Datasetter.
You receive a dataset row with errors and specific fix instructions.
Apply each fix by calling apply_fix with the field name and the corrected value.
Follow the fix instruction exactly. Do not add information that wasn't asked for.
Do not change fields that aren't mentioned in the errors.
"""


class Fixer:

    def __init__(self, router: LiteLLMRouter, emitter: Emitter):
        self.router  = router
        self.emitter = emitter

    async def run(
        self,
        error_rows: List[DatasetRow],
        config: JobConfig,
        seed_pack: SeedPack,
        verifier,   # Verifier instance for re-verification
    ) -> List[DatasetRow]:
        """
        Fix all error rows, then re-verify. Loops until max_fix_rounds.

        Args:
            error_rows: Rows with status=ERROR.
            config:     Job config.
            seed_pack:  For blueprint access during re-verification.
            verifier:   Verifier instance — used to re-check after fixing.

        Returns:
            All rows with updated statuses. Rows that couldn't be fixed
            keep status=ERROR and are surfaced to the user.
        """
        if not error_rows:
            return []

        self.emitter.agent_status(
            AgentID.FIXER, AgentStatus.RUNNING,
            current_task=f"Fixing {len(error_rows)} rows"
        )
        self.emitter.log(
            AgentID.FIXER,
            f"Fixing {len(error_rows)} rows. Max rounds: {config.max_fix_rounds}."
        )

        rows = error_rows
        max_rounds = config.max_fix_rounds

        for round_num in range(1, max_rounds + 1):
            still_broken = [r for r in rows if r.status == RowStatus.ERROR]
            if not still_broken:
                break

            self.emitter.log(
                AgentID.FIXER,
                f"Fix round {round_num}/{max_rounds}: {len(still_broken)} rows."
            )

            # Fix all broken rows concurrently
            sem = asyncio.Semaphore(4)

            async def fix_one(row: DatasetRow) -> DatasetRow:
                async with sem:
                    return await self._fix_row(row, config)

            fixed_rows = await asyncio.gather(
                *[fix_one(r) for r in still_broken],
                return_exceptions=True
            )

            # Merge fixed rows back
            fixed_map: Dict[int, DatasetRow] = {}
            for original, result in zip(still_broken, fixed_rows):
                if isinstance(result, Exception):
                    log.error(f"Fixer failed for row {original.id}: {result}")
                    fixed_map[original.id] = original
                else:
                    fixed_map[original.id] = result

            rows = [fixed_map.get(r.id, r) for r in rows]

            # Re-verify the fixed rows
            self.emitter.log(AgentID.FIXER, f"Re-verifying {len(still_broken)} fixed rows.")
            rows_to_reverify = [r for r in rows if r.id in fixed_map]

            reverified = await verifier.verify_rows(
                rows=rows_to_reverify,
                config=config,
                blueprint=seed_pack.blueprint,
            )

            # Merge re-verified results back
            reverified_map = {r.id: r for r in reverified}
            rows = [reverified_map.get(r.id, r) for r in rows]

            # Persist updated rows
            for row in rows:
                self.emitter.row_update(row)
                await update_row(config.job_id, row)

            now_ok    = sum(1 for r in rows if r.status == RowStatus.OK)
            still_err = sum(1 for r in rows if r.status == RowStatus.ERROR)
            self.emitter.log(
                AgentID.FIXER,
                f"Round {round_num}: {now_ok} passing, {still_err} still failing."
            )

        # Surface any rows that couldn't be fixed
        unfixable = [r for r in rows if r.status == RowStatus.ERROR]
        if unfixable:
            self.emitter.warning(
                "Fixer",
                f"{len(unfixable)} row(s) could not be fixed after {max_rounds} rounds. "
                f"Manual review required."
            )

        self.emitter.agent_status(
            AgentID.FIXER, AgentStatus.DONE,
            current_task=f"{len(rows)-len(unfixable)}/{len(rows)} fixed"
        )

        return rows

    async def _fix_row(self, row: DatasetRow, config: JobConfig) -> DatasetRow:
        """Fix a single row by applying all its Verifier errors."""
        if not row.errors:
            row.status = RowStatus.OK
            return row

        row.status = RowStatus.FIXING
        self.emitter.row_update(row)

        # Apply fixes error by error
        # Group by field to avoid conflicting fixes
        fixes_by_field: Dict[str, List[VerifierError]] = {}
        for error in row.errors:
            fixes_by_field.setdefault(error.field, []).append(error)

        for field, errors in fixes_by_field.items():
            current_value = str(row.fields.get(field, ""))

            # Build fix message
            error_descriptions = "\n".join(
                f"- {e.error_type.value}: {e.description}\n  Fix: {e.fix_instruction}"
                for e in errors
            )

            user_message = (
                f"Row ID: {row.id}\n"
                f"Category: {row.category}\n\n"
                f"Field to fix: {field}\n"
                f"Current value:\n{current_value}\n\n"
                f"Errors to fix:\n{error_descriptions}\n\n"
                f"Call apply_fix with the corrected value for field '{field}'."
            )

            try:
                tool_calls, model = await self.router.complete_tools(
                    agent_id=AgentID.FIXER,
                    messages=[{"role": "user", "content": user_message}],
                    tools=[FIX_TOOL],
                    system=SYSTEM_PROMPT,
                    temperature=0.2,
                    max_tokens=2048,
                )

                for tc in tool_calls:
                    if tc["name"] == "apply_fix":
                        args = tc["arguments"]
                        fixed_field = args.get("field", field)
                        fixed_value = args.get("fixed_value", current_value)
                        row.fields[fixed_field] = fixed_value
                        log.debug(f"Row {row.id}.{fixed_field}: fixed.")

            except Exception as e:
                log.error(f"Fixer tool call failed for row {row.id} field {field}: {e}")
                # Fallback: try a plain completion
                try:
                    fixed_value = await self._fix_plain(
                        field=field,
                        current_value=current_value,
                        errors=errors,
                    )
                    row.fields[field] = fixed_value
                except Exception as e2:
                    log.error(f"Fixer plain fallback also failed: {e2}")
                    # Leave field as-is; re-verification will catch it

        row.fix_rounds += 1
        return row

    async def _fix_plain(
        self,
        field: str,
        current_value: str,
        errors: List[VerifierError],
    ) -> str:
        """
        Fallback: plain text completion when tool calls fail.
        Returns just the fixed field value as a string.
        """
        error_text = "\n".join(
            f"- {e.fix_instruction}" for e in errors
        )

        prompt = (
            f"Fix this field value according to these instructions:\n\n"
            f"Field: {field}\n"
            f"Current value: {current_value}\n\n"
            f"Instructions:\n{error_text}\n\n"
            f"Output ONLY the corrected field value. Nothing else."
        )

        text, _ = await self.router.complete(
            agent_id=AgentID.FIXER,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=1024,
        )

        return text.strip()
