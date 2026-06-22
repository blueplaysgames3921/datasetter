"""
agents/scripter.py

The Scripter generates the full dataset from the seed pack.

Optimizations:
  - Blueprint trimmed per category via trim_blueprint_for_category
  - Stable context (blueprint + schema + seeds) sent as system prompt
    so Anthropic/Groq prefix caching applies across batches of the same category
  - Categories processed with controlled concurrency via optimal_parallelism
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List

from core.litellm_router import LiteLLMRouter
from core.models import (
    AgentID,
    AgentStatus,
    DatasetRow,
    JobConfig,
    RowStatus,
    SeedExample,
    SeedPack,
)
from core.optimizations import optimal_parallelism, trim_blueprint_for_category
from utils.events import Emitter
from utils.linter import Linter
from utils.storage import append_rows

log = logging.getLogger(__name__)

BATCH_SIZE = 20

SYSTEM_PROMPT = """You are the Scripter agent in Datasetter, an AI dataset generation pipeline.

Your job is to generate high-quality, diverse dataset rows based on the context provided.

Critical rules:
1. Generate rows INSPIRED by the seeds — do NOT copy them. Each row must be unique.
2. Maintain the quality level, tone, and style of the seeds exactly.
3. Fill every required field completely. No placeholders, no empty fields.
4. Vary content, phrasing, complexity, and approach across rows.
5. Follow per-category instructions and all constraints exactly.
6. Follow the blueprint quality bar — mediocre rows fail the Verifier.

Output ONLY valid JSON:
{
  "rows": [
    { "fields": { "field_name": "field value", ... } },
    ...
  ]
}

Generate exactly the requested number of rows. No preamble, no notes outside the JSON."""


class Scripter:

    def __init__(self, router: LiteLLMRouter, emitter: Emitter):
        self.router  = router
        self.emitter = emitter

    async def run(
        self,
        config: JobConfig,
        seed_pack: SeedPack,
    ) -> List[DatasetRow]:
        """
        Generate the full dataset from the seed pack.
        Categories are processed with controlled parallelism.
        """
        linter     = Linter(config)
        total_cats = len(seed_pack.categories)

        self.emitter.agent_status(
            AgentID.SCRIPTER, AgentStatus.RUNNING,
            current_task=f"0/{total_cats} categories",
        )

        hw_tier    = self.router.hardware.tier if self.router.hardware else "mid"
        assignment = self.router._resolve_assignment(AgentID.SCRIPTER)
        is_local   = self.router._pick_mode(assignment).value == "local"
        concurrency = optimal_parallelism(
            n_items=total_cats,
            model_str=assignment.cloud_model or assignment.local_model or "",
            is_local=is_local,
            hardware_tier=hw_tier,
        )

        semaphore       = asyncio.Semaphore(concurrency)
        all_rows:  List[DatasetRow] = []
        row_id_counter  = [0]
        completed_cats  = [0]

        async def process_category(category: str) -> List[DatasetRow]:
            async with semaphore:
                target_rows = seed_pack.category_targets.get(category, 0)
                if target_rows <= 0:
                    return []

                cat_seeds = [s for s in seed_pack.seeds if s.category == category]
                if not cat_seeds:
                    log.warning(f"No seeds for '{category}'. Skipping.")
                    return []

                start_id = row_id_counter[0]
                row_id_counter[0] += target_rows

                completed_cats[0] += 1
                self.emitter.log(
                    AgentID.SCRIPTER,
                    f"Category {completed_cats[0]}/{total_cats}: '{category}' "
                    f"— {target_rows} rows, {len(cat_seeds)} seeds.",
                )
                self.emitter.agent_status(
                    AgentID.SCRIPTER, AgentStatus.RUNNING,
                    current_task=f"'{category}' ({completed_cats[0]}/{total_cats})",
                )

                cat_rows = await self._generate_category(
                    category=category,
                    target_rows=target_rows,
                    seeds=cat_seeds,
                    seed_pack=seed_pack,
                    config=config,
                    linter=linter,
                    start_id=start_id,
                )

                await append_rows(config.job_id, cat_rows)

                ok_count  = sum(1 for r in cat_rows if r.status != RowStatus.ERROR)
                err_count = sum(1 for r in cat_rows if r.status == RowStatus.ERROR)
                self.emitter.log(
                    AgentID.SCRIPTER,
                    f"'{category}' complete: {ok_count} OK, {err_count} linting errors.",
                )
                return cat_rows

        results = await asyncio.gather(
            *[process_category(cat) for cat in seed_pack.categories],
            return_exceptions=True,
        )

        for cat, result in zip(seed_pack.categories, results):
            if isinstance(result, Exception):
                log.error(f"Scripter failed for category '{cat}': {result}")
                self.emitter.log(AgentID.SCRIPTER, f"Category '{cat}' failed: {result}. Skipping.")
            else:
                all_rows.extend(result)

        all_rows.sort(key=lambda r: r.id)

        self.emitter.log(
            AgentID.SCRIPTER,
            f"All categories done. Total: {len(all_rows)} rows generated.",
        )
        self.emitter.agent_status(
            AgentID.SCRIPTER, AgentStatus.DONE,
            current_task=f"{len(all_rows)} rows generated",
        )
        return all_rows

    async def _generate_category(
        self,
        category: str,
        target_rows: int,
        seeds: List[SeedExample],
        seed_pack: SeedPack,
        config: JobConfig,
        linter: Linter,
        start_id: int,
    ) -> List[DatasetRow]:
        all_rows: List[DatasetRow] = []
        remaining  = target_rows
        current_id = start_id
        batch_num  = 0

        # Build stable system prompt — cached by Anthropic/Groq between batches
        category_system = self._build_category_system(category, seeds, seed_pack, config)

        while remaining > 0:
            batch_size = min(BATCH_SIZE, remaining)
            batch_num += 1

            try:
                raw_rows = await self._generate_batch(
                    category_system=category_system,
                    batch_size=batch_size,
                    already_generated=len(all_rows),
                    total_target=target_rows,
                )
            except Exception as e:
                log.error(f"Batch {batch_num} failed for '{category}': {e}. Retrying.")
                self.emitter.log(AgentID.SCRIPTER, f"  Batch {batch_num} failed: {e}. Retrying.")
                try:
                    raw_rows = await self._generate_batch(
                        category_system=category_system,
                        batch_size=batch_size,
                        already_generated=len(all_rows),
                        total_target=target_rows,
                    )
                except Exception as e2:
                    log.error(f"Retry failed for batch {batch_num}: {e2}. Skipping.")
                    remaining -= batch_size
                    continue

            batch_rows: List[DatasetRow] = []
            for raw in raw_rows:
                row = DatasetRow(
                    id=current_id,
                    status=RowStatus.PENDING,
                    category=category,
                    fields=raw.get("fields", {}),
                    batch_id=batch_num,
                )
                batch_rows.append(row)
                current_id += 1

            lint_results   = linter.lint_batch(batch_rows)
            clean, flagged = linter.apply_results(batch_rows, lint_results)
            summary        = linter.summary(lint_results)

            if summary["failed"] > 0:
                self.emitter.log(
                    AgentID.SCRIPTER,
                    f"  Batch {batch_num}: {summary['passed']}/{summary['total']} lint. "
                    f"{summary['failed']} flagged ({summary['error_types']}).",
                )
            else:
                self.emitter.log(
                    AgentID.SCRIPTER,
                    f"  Batch {batch_num}: {summary['total']} rows — lint passed.",
                )

            all_rows.extend(batch_rows)
            self.emitter.rows_batch(batch_rows)
            remaining -= len(raw_rows)

        return all_rows

    async def _generate_batch(
        self,
        category_system: str,
        batch_size: int,
        already_generated: int,
        total_target: int,
    ) -> List[Dict[str, Any]]:
        """
        Generate one batch. The category_system is the cacheable system prompt.
        Only the minimal progress counter changes between calls.
        """
        user_message = (
            f"Already generated: {already_generated}/{total_target} rows.\n"
            f"Generate {batch_size} more rows now. "
            f"Make each row meaningfully different. "
            f"Vary topic, complexity, phrasing, and approach."
        )

        raw, _ = await self.router.complete_json(
            agent_id=AgentID.SCRIPTER,
            messages=[{"role": "user", "content": user_message}],
            system=category_system,
            temperature=0.9,
            max_tokens=8000,
            use_cache=True,
        )

        rows = raw.get("rows", [])
        if not isinstance(rows, list):
            raise ValueError(f"Scripter returned invalid rows type: {type(rows)}")
        return rows

    def _build_category_system(
        self,
        category: str,
        seeds: List[SeedExample],
        seed_pack: SeedPack,
        config: JobConfig,
    ) -> str:
        """
        Build the stable system prompt for a category.
        Sent as the `system` parameter — unchanged between batches
        so Anthropic/Groq will cache it after the first call.
        Blueprint trimmed to just the category-relevant section.
        """
        spec  = seed_pack.generation_spec
        parts = []

        # Trimmed blueprint (header + this category only)
        trimmed = trim_blueprint_for_category(seed_pack.blueprint, category)
        parts.append("## Blueprint")
        parts.append(trimmed)

        parts.append(f"\n## Category: {category}")
        cat_instructions = self._extract_cat_instructions(category, seed_pack.blueprint)
        if cat_instructions:
            parts.append(f"Instructions:\n{cat_instructions}")

        parts.append("\n## Field Schema")
        for f in spec.get("fields", []):
            line = f"- {f['name']}: {f.get('description', '')}"
            if f.get("min_length"):
                line += f" [min {f['min_length']} chars]"
            if f.get("max_length"):
                line += f" [max {f['max_length']} chars]"
            parts.append(line)

        constraints = spec.get("constraints", [])
        if constraints:
            parts.append("\n## Constraints")
            parts.extend(f"- {c}" for c in constraints)

        neg = spec.get("negative_constraints", [])
        if neg:
            parts.append("\n## Negative Constraints")
            parts.extend(f"- {c}" for c in neg)

        parts.append(f"\n## Seed Examples for '{category}'")
        parts.append("Match this quality and style. Do NOT copy — generate new content.")
        for i, seed in enumerate(seeds[:8], 1):
            edge = f"  ← edge case: {seed.edge_case_type}" if seed.is_edge_case else ""
            parts.append(f"\n[Seed {i}{edge}]")
            for field, value in seed.fields.items():
                val_str = str(value)
                if len(val_str) > 300:
                    val_str = val_str[:300] + "..."
                parts.append(f"  {field}: {val_str}")

        return "\n".join(parts)

    def _extract_cat_instructions(self, category: str, blueprint: str) -> str:
        lines      = blueprint.split("\n")
        in_section = False
        section    = []
        for line in lines:
            if line.strip().lower() == f"### {category.lower()}":
                in_section = True
                continue
            if in_section:
                if line.startswith("### "):
                    break
                section.append(line)
        return "\n".join(section).strip()

    # Backward-compat alias used by main.py regenerate_row
    def _build_category_context(
        self,
        category: str,
        seeds: List[SeedExample],
        cat_instructions: str,
        seed_pack: SeedPack,
        config: JobConfig,
    ) -> str:
        return self._build_category_system(category, seeds, seed_pack, config)
