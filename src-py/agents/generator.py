"""
agents/generator.py

The Generator produces seed examples that Scripter uses as the blueprint
for scaling to the full dataset.

Two-pass approach:
  Pass 1 — Category mapping: given the GenerationSpec, identify all
            categories, their characteristics, and edge case types.
            Think of this as building a content plan.

  Pass 2 — Seed generation: for each category, generate 3-8 high-quality,
            maximally diverse seed examples. Cover: typical cases, edge
            cases, boundary cases, and representative variations.

Max seeds: 150 total (enforced). More seeds = more diversity in output.
Seeds are not part of the final dataset — they are the DNA Scripter clones.

The Generator also writes per-category generation instructions that
Scripter receives alongside each seed batch.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Dict, List, Optional

from agents.interpreter import GenerationSpec
from core.litellm_router import LiteLLMRouter
from core.models import AgentID, AgentStatus, JobConfig, SeedExample, SeedPack
from utils.events import Emitter

log = logging.getLogger(__name__)

MAX_SEEDS = 150


# ─── System prompts ───────────────────────────────────────────────────────────

PASS1_SYSTEM = """You are the Generator agent (Pass 1) in Datasetter, an AI dataset generation pipeline.

Your job in this pass is to build a detailed content plan for dataset generation.
You receive a GenerationSpec and must produce a structured category map.

Output ONLY valid JSON in this exact schema:

{
  "categories": [
    {
      "name": "Category Name",
      "description": "What this category covers and why it matters",
      "characteristics": [
        "Key characteristic 1",
        "Key characteristic 2"
      ],
      "edge_case_types": [
        "edge case type 1: description of what makes it an edge case",
        "edge case type 2: ..."
      ],
      "tone_notes": "Specific tone or style guidance for this category",
      "difficulty_range": "easy to hard / all similar / varies significantly",
      "seed_count": 6,
      "row_target": 120,
      "generation_instructions": "Specific instructions for Scripter when generating rows in this category. Be detailed — cover what makes a good row, what to vary, what to avoid, how to handle ambiguity."
    }
  ],
  "global_notes": "Overarching notes that apply to all categories",
  "diversity_strategy": "How to ensure diversity across the full dataset",
  "quality_bar": "What separates a good row from a mediocre one in this dataset"
}

Be thorough. Scripter and Verifier both read generation_instructions.
A weak plan produces a weak dataset.
"""

PASS2_SYSTEM = """You are the Generator agent (Pass 2) in Datasetter, an AI dataset generation pipeline.

Your job in this pass is to generate high-quality, maximally diverse seed examples
for a specific category. These seeds will be used by Scripter as templates
to generate the full dataset at scale.

Rules:
- Cover the FULL range of variation within the category
- Include typical cases AND edge cases AND boundary cases
- Each seed must be meaningfully different from all others
- Seeds must match the field schema exactly — no extra fields, no missing fields
- Quality must be exemplary — Scripter mimics your style and quality level
- Avoid repetitive patterns, similar phrasings, or samey structure
- If a seed is an edge case, set is_edge_case=true and name the edge_case_type

Output ONLY valid JSON:

{
  "seeds": [
    {
      "category": "Category Name",
      "fields": {
        "field_name": "field value",
        ...
      },
      "is_edge_case": false,
      "edge_case_type": null,
      "quality_notes": "Brief note on what makes this seed good / what it demonstrates"
    },
    ...
  ]
}

The seeds array must contain exactly the requested number of seeds.
Do not include any text outside the JSON object.
"""


# ─── Generator agent ──────────────────────────────────────────────────────────

class Generator:

    def __init__(self, router: LiteLLMRouter, emitter: Emitter):
        self.router  = router
        self.emitter = emitter

    async def run(
        self,
        config: JobConfig,
        spec: GenerationSpec,
        research_context: Optional[str] = None,
    ) -> SeedPack:
        """
        Run both passes and return a SeedPack.

        Args:
            config:           Full job config.
            spec:             GenerationSpec from Interpreter.
            research_context: Optional research brief from Researcher.

        Returns:
            SeedPack ready for Scripter.
        """
        self.emitter.agent_status(
            AgentID.GENERATOR, AgentStatus.RUNNING,
            current_task="Pass 1: category mapping"
        )
        self.emitter.log(AgentID.GENERATOR, "Pass 1: mapping categories and planning content.")

        # ── Pass 1: category map ──────────────────────────────────────────────
        category_map = await self._pass1_category_map(spec, research_context)

        categories = category_map.get("categories", [])
        if not categories:
            raise RuntimeError("Generator Pass 1 returned no categories.")

        self.emitter.log(
            AgentID.GENERATOR,
            f"Pass 1 complete. {len(categories)} categories mapped. "
            "Pass 2: generating seeds."
        )
        self.emitter.agent_status(
            AgentID.GENERATOR, AgentStatus.RUNNING,
            current_task="Pass 2: generating seeds"
        )

        # ── Allocate seed budget ──────────────────────────────────────────────
        seed_budget = min(config.seed_count, MAX_SEEDS)
        categories  = self._allocate_seeds(categories, seed_budget)

        # ── Pass 2: seed generation ───────────────────────────────────────────
        # Run categories concurrently but cap parallelism
        semaphore = asyncio.Semaphore(3)
        all_seeds: List[SeedExample] = []
        seed_id   = 0

        async def generate_category(cat: dict) -> List[SeedExample]:
            async with semaphore:
                return await self._pass2_generate_seeds(
                    cat=cat,
                    spec=spec,
                    research_context=research_context,
                    config=config,
                )

        results = await asyncio.gather(
            *[generate_category(cat) for cat in categories],
            return_exceptions=True
        )

        for cat, result in zip(categories, results):
            if isinstance(result, Exception):
                self.emitter.log(
                    AgentID.GENERATOR,
                    f"Seed generation failed for '{cat['name']}': {result}. Skipping."
                )
                log.error(f"Generator pass 2 failed for {cat['name']}: {result}")
                continue

            for seed in result:
                seed.id = seed_id
                seed_id += 1
                all_seeds.append(seed)
            self.emitter.log(
                AgentID.GENERATOR,
                f"'{cat['name']}': {len(result)} seeds generated."
            )

        if not all_seeds:
            raise RuntimeError("Generator produced no seeds. Cannot continue.")

        # ── Build SeedPack ────────────────────────────────────────────────────
        # Per-category row targets come from spec, cross-checked with category map
        category_targets = self._build_targets(categories, spec, config.total_rows)

        # Blueprint = spec blueprint + global notes from pass 1
        blueprint = self._build_blueprint(spec, category_map)

        seed_pack = SeedPack(
            seeds=all_seeds,
            categories=[cat["name"] for cat in categories],
            category_targets=category_targets,
            generation_spec=spec.to_dict(),
            blueprint=blueprint,
        )

        self.emitter.log(
            AgentID.GENERATOR,
            f"Seed pack ready. {len(all_seeds)} seeds across "
            f"{len(categories)} categories. "
            f"Edge cases: {sum(1 for s in all_seeds if s.is_edge_case)}."
        )
        self.emitter.agent_status(
            AgentID.GENERATOR, AgentStatus.DONE,
            current_task=f"{len(all_seeds)} seeds ready"
        )

        return seed_pack

    # ── Pass 1 ────────────────────────────────────────────────────────────────

    async def _pass1_category_map(
        self,
        spec: GenerationSpec,
        research_context: Optional[str],
    ) -> dict:
        user_content = self._build_pass1_message(spec, research_context)

        raw, model = await self.router.complete_json(
            agent_id=AgentID.GENERATOR,
            messages=[{"role": "user", "content": user_content}],
            system=PASS1_SYSTEM,
            temperature=0.5,
            max_tokens=6000,
        )

        self.emitter.agent_status(
            AgentID.GENERATOR, AgentStatus.RUNNING,
            model_used=model,
            current_task="Pass 1 complete"
        )
        return raw

    def _build_pass1_message(self, spec: GenerationSpec, research_context: Optional[str]) -> str:
        parts = ["## Generation Spec"]
        parts.append(f"Description: {spec.dataset_description}")
        parts.append(f"Use case: {spec.use_case}")
        parts.append(f"Total rows: {spec.total_rows}")
        parts.append(f"Categories requested: {len(spec.categories)} — {', '.join(spec.categories) if spec.categories else 'auto-detect'}")
        parts.append(f"Language: {spec.language}")
        parts.append(f"Tone: {spec.tone} / {spec.formality}")
        parts.append(f"Target audience: {spec.target_audience}")
        parts.append(f"Domain expertise: {spec.domain_expertise}")

        parts.append("\n## Fields")
        for f in spec.fields:
            parts.append(
                f"- {f['name']}: {f.get('description','')} "
                f"(min_length={f.get('min_length','none')}, max_length={f.get('max_length','none')})"
            )

        if spec.constraints:
            parts.append("\n## Constraints")
            for c in spec.constraints:
                parts.append(f"- {c}")

        if spec.negative_constraints:
            parts.append("\n## Negative Constraints (avoid)")
            for c in spec.negative_constraints:
                parts.append(f"- {c}")

        if spec.edge_case_types:
            parts.append("\n## Edge Case Types to Cover")
            for e in spec.edge_case_types:
                parts.append(f"- {e}")

        if research_context:
            parts.append(f"\n## Research Context\n{research_context[:4000]}")

        parts.append("\n## Blueprint\n" + spec.blueprint[:3000])

        parts.append(
            "\nNow produce the category map JSON. "
            "Be specific in generation_instructions — Scripter reads this for every row it generates."
        )

        return "\n".join(parts)

    # ── Pass 2 ────────────────────────────────────────────────────────────────

    async def _pass2_generate_seeds(
        self,
        cat: dict,
        spec: GenerationSpec,
        research_context: Optional[str],
        config: JobConfig,
    ) -> List[SeedExample]:
        seed_count = cat.get("seed_count", 5)
        user_content = self._build_pass2_message(cat, spec, research_context, seed_count)

        raw, _ = await self.router.complete_json(
            agent_id=AgentID.GENERATOR,
            messages=[{"role": "user", "content": user_content}],
            system=PASS2_SYSTEM,
            temperature=0.85,   # Higher temp for diversity
            max_tokens=8000,
        )

        seeds_raw = raw.get("seeds", [])
        seeds     = []

        for i, s in enumerate(seeds_raw):
            try:
                seed = SeedExample(
                    id=0,  # Will be assigned by caller
                    category=s.get("category", cat["name"]),
                    fields=s.get("fields", {}),
                    is_edge_case=s.get("is_edge_case", False),
                    edge_case_type=s.get("edge_case_type"),
                )
                seeds.append(seed)
            except Exception as e:
                log.warning(f"Malformed seed {i} in '{cat['name']}': {e}")

        return seeds

    def _build_pass2_message(
        self,
        cat: dict,
        spec: GenerationSpec,
        research_context: Optional[str],
        seed_count: int,
    ) -> str:
        parts = []

        parts.append(f"## Category: {cat['name']}")
        parts.append(f"Description: {cat.get('description', '')}")
        parts.append(f"Characteristics: {', '.join(cat.get('characteristics', []))}")
        parts.append(f"Tone notes: {cat.get('tone_notes', '')}")
        parts.append(f"Difficulty range: {cat.get('difficulty_range', '')}")

        parts.append("\n## Generation Instructions for this Category")
        parts.append(cat.get("generation_instructions", ""))

        parts.append("\n## Edge Case Types to Include")
        for ect in cat.get("edge_case_types", []):
            parts.append(f"- {ect}")

        parts.append("\n## Field Schema")
        for f in spec.fields:
            line = (
                f"- {f['name']} ({f.get('description', '')})"
            )
            if f.get("min_length"):
                line += f" min={f['min_length']} chars"
            if f.get("max_length"):
                line += f" max={f['max_length']} chars"
            if f.get("examples"):
                line += f"\n  Examples: {'; '.join(f['examples'][:2])}"
            parts.append(line)

        parts.append("\n## Overall Constraints")
        for c in spec.constraints:
            parts.append(f"- {c}")

        parts.append("\n## Negative Constraints (avoid)")
        for c in spec.negative_constraints:
            parts.append(f"- {c}")

        if research_context:
            parts.append("\n## Research Context (use accurate facts from here)")
            parts.append(research_context[:2000])

        parts.append("\n## Quality Bar")
        parts.append(spec.blueprint[:1000])

        parts.append(
            f"\nGenerate exactly {seed_count} seeds for the '{cat['name']}' category. "
            "Make each one meaningfully different. "
            "Ensure edge cases are represented. "
            "Quality must be exemplary — Scripter will clone your style."
        )

        return "\n".join(parts)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _allocate_seeds(self, categories: List[dict], total_budget: int) -> List[dict]:
        """
        Distribute seed budget proportionally, minimum 3 per category.
        Total assigned seeds will equal exactly total_budget.
        """
        n           = len(categories)
        if n == 0:
            return categories

        min_per_cat = 3
        # If budget is too small to give every category minimum, distribute evenly
        if total_budget <= n * min_per_cat:
            base = max(1, total_budget // n)
            for cat in categories:
                cat["seed_count"] = base
            # Distribute remaining to first categories
            remaining = total_budget - base * n
            for i in range(remaining):
                categories[i]["seed_count"] += 1
            return categories

        # Proportional allocation weighted by row_target
        targets      = [max(1, cat.get("row_target", 1)) for cat in categories]
        total_target = sum(targets)
        extra_budget = total_budget - n * min_per_cat

        for cat, target in zip(categories, targets):
            proportion      = target / total_target
            cat["seed_count"] = min_per_cat + round(extra_budget * proportion)

        # Fix rounding — adjust largest category to make total exact
        current_total = sum(cat["seed_count"] for cat in categories)
        diff = total_budget - current_total
        if diff != 0:
            largest = max(categories, key=lambda c: c["seed_count"])
            largest["seed_count"] = max(min_per_cat, largest["seed_count"] + diff)

        return categories

    def _build_targets(
        self,
        categories: List[dict],
        spec: GenerationSpec,
        total_rows: int,
    ) -> Dict[str, int]:
        """
        Build category → target_row_count mapping.
        Prefers spec.category_targets, falls back to category map row_targets.
        """
        targets: Dict[str, int] = {}

        # Use spec targets if they exist and cover all categories
        if spec.category_targets:
            for cat in categories:
                name = cat["name"]
                targets[name] = spec.category_targets.get(name, cat.get("row_target", 0))
        else:
            for cat in categories:
                targets[cat["name"]] = cat.get("row_target", 0)

        # Normalise to total_rows
        total = sum(targets.values())
        if total == 0:
            # Equal distribution
            per_cat = total_rows // len(categories)
            for name in targets:
                targets[name] = per_cat
            total = sum(targets.values())

        if total != total_rows:
            factor = total_rows / total
            for name in targets:
                targets[name] = max(1, round(targets[name] * factor))
            # Fix rounding
            diff = total_rows - sum(targets.values())
            if diff != 0:
                largest = max(targets, key=targets.get)
                targets[largest] += diff

        return targets

    def _build_blueprint(self, spec: GenerationSpec, category_map: dict) -> str:
        """
        Combine the Interpreter's blueprint with Generator's pass 1 global notes.
        This is the definitive guide Scripter and Verifier both read.
        """
        parts = [spec.blueprint]

        global_notes = category_map.get("global_notes", "")
        if global_notes:
            parts.append(f"\n## Generator Notes\n{global_notes}")

        diversity_strategy = category_map.get("diversity_strategy", "")
        if diversity_strategy:
            parts.append(f"\n## Diversity Strategy\n{diversity_strategy}")

        quality_bar = category_map.get("quality_bar", "")
        if quality_bar:
            parts.append(f"\n## Quality Bar\n{quality_bar}")

        # Add per-category generation instructions
        cat_sections = []
        for cat in category_map.get("categories", []):
            inst = cat.get("generation_instructions", "")
            if inst:
                cat_sections.append(f"### {cat['name']}\n{inst}")

        if cat_sections:
            parts.append("\n## Per-Category Instructions\n" + "\n\n".join(cat_sections))

        return "\n\n".join(parts)
