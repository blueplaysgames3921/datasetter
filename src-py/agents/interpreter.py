"""
agents/interpreter.py

The Interpreter is the first agent in every pipeline.
It receives the raw user prompt + any pre-processed context from Analyser
and produces a fully structured GenerationSpec that every downstream
agent reads from.

Responsibilities:
  - Parse intent (what kind of dataset, what format, what use case)
  - Determine scope (row count, category structure, field schema)
  - Decide which other agents need to activate (Researcher, etc.)
  - Write the blueprint — a plain-language guide Scripter and Verifier
    both receive so they share the same understanding of the job
  - Validate that the job is feasible given the config
  - Check and normalise output format, language, constraints
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from core.litellm_router import LiteLLMRouter
from core.models import (
    AgentID,
    AgentStatus,
    FieldConstraint,
    JobConfig,
    PipelineMode,
)
from utils.events import Emitter

log = logging.getLogger(__name__)


# ─── Output schema ────────────────────────────────────────────────────────────


class GenerationSpec:
    """
    Structured output of the Interpreter.
    Passed to Generator, Scripter, and Verifier.
    """

    def __init__(self, raw: dict):
        self.pipeline_mode: PipelineMode     = PipelineMode(raw.get("pipeline_mode", "vibe"))
        self.needs_researcher: bool          = raw.get("needs_researcher", False)
        self.researcher_queries: List[str]   = raw.get("researcher_queries", [])

        self.dataset_description: str        = raw.get("dataset_description", "")
        self.use_case: str                   = raw.get("use_case", "")
        self.language: str                   = raw.get("language", "English")

        self.categories: List[str]           = raw.get("categories", [])
        self.category_targets: Dict[str,int] = raw.get("category_targets", {})
        self.total_rows: int                 = raw.get("total_rows", 500)

        # Field schema
        self.fields: List[Dict[str,Any]]     = raw.get("fields", [])
        # [{ "name": "instruction", "description": "...", "min_length": 20, ... }]

        # Tone and style
        self.tone: str                       = raw.get("tone", "neutral")
        self.formality: str                  = raw.get("formality", "neutral")
        self.target_audience: str            = raw.get("target_audience", "general")
        self.domain_expertise: str           = raw.get("domain_expertise", "mixed")

        # Constraints
        self.constraints: List[str]          = raw.get("constraints", [])
        self.negative_constraints: List[str] = raw.get("negative_constraints", [])
        self.edge_case_types: List[str]      = raw.get("edge_case_types", [])

        # The shared understanding document
        self.blueprint: str                  = raw.get("blueprint", "")

        # Warnings / notes back to orchestrator
        self.warnings: List[str]             = raw.get("warnings", [])
        self.feasible: bool                  = raw.get("feasible", True)
        self.infeasibility_reason: str       = raw.get("infeasibility_reason", "")

    def to_dict(self) -> dict:
        return {
            "pipeline_mode": self.pipeline_mode.value,
            "needs_researcher": self.needs_researcher,
            "researcher_queries": self.researcher_queries,
            "dataset_description": self.dataset_description,
            "use_case": self.use_case,
            "language": self.language,
            "categories": self.categories,
            "category_targets": self.category_targets,
            "total_rows": self.total_rows,
            "fields": self.fields,
            "tone": self.tone,
            "formality": self.formality,
            "target_audience": self.target_audience,
            "domain_expertise": self.domain_expertise,
            "constraints": self.constraints,
            "negative_constraints": self.negative_constraints,
            "edge_case_types": self.edge_case_types,
            "blueprint": self.blueprint,
            "warnings": self.warnings,
            "feasible": self.feasible,
            "infeasibility_reason": self.infeasibility_reason,
        }


# ─── System prompt ────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are the Interpreter agent in Datasetter, an AI-augmented dataset generation pipeline.

Your job is to receive a user's dataset request and produce a fully structured generation specification
that every downstream agent will use. You are the single source of truth for what this dataset is,
what it contains, how it should be structured, and what quality it must meet.

You must output ONLY valid JSON. No preamble, no markdown, no explanation outside the JSON.

## Your output schema:

{
  "pipeline_mode": "vibe" | "file" | "research" | "edit" | "minimal",
  "needs_researcher": true | false,
  "researcher_queries": ["query 1", "query 2"],   // only if needs_researcher=true

  "dataset_description": "clear 2-3 sentence description of what this dataset contains",
  "use_case": "what this dataset will be used for",
  "language": "English",

  "categories": ["Category A", "Category B", ...],
  "category_targets": { "Category A": 120, "Category B": 80, ... },  // must sum to total_rows
  "total_rows": 500,

  "fields": [
    {
      "name": "instruction",
      "description": "The input/question/prompt for this example",
      "required": true,
      "min_length": 20,
      "max_length": 500,
      "examples": ["example 1", "example 2"],
      "forbidden_patterns": []
    },
    {
      "name": "response",
      "description": "The expected output/answer",
      "required": true,
      "min_length": 50,
      "max_length": 2000,
      "examples": [],
      "forbidden_patterns": ["TODO", "PLACEHOLDER"]
    }
  ],

  "tone": "educational" | "conversational" | "formal" | "technical" | "neutral",
  "formality": "formal" | "neutral" | "casual",
  "target_audience": "beginners" | "intermediate" | "experts" | "general" | "mixed",
  "domain_expertise": "low" | "medium" | "high" | "mixed",

  "constraints": [
    "Each response must include at least one concrete example",
    "Instruction must be a complete question",
    ...
  ],
  "negative_constraints": [
    "Do not generate examples about deprecated APIs",
    ...
  ],
  "edge_case_types": [
    "ambiguous questions with multiple valid answers",
    "very short instructions",
    "questions requiring domain-specific knowledge"
  ],

  "blueprint": "A comprehensive plain-language guide (500-1000 words) that explains this dataset to
                Scripter and Verifier. Cover: what the dataset is, what each field contains,
                quality standards, tone, examples of good vs bad rows, how to handle edge cases,
                what the verifier should be strict about.",

  "warnings": [],   // any concerns about feasibility, ambiguity, or scope
  "feasible": true,
  "infeasibility_reason": ""  // explain if feasible=false
}

## Guidelines:

CATEGORIES: Identify 4-10 diverse categories that cover the topic space well. Distribute rows
proportionally — no category should be less than 3% or more than 40% of total rows.

FIELDS: Infer the right field schema from the prompt. For instruction/response datasets, use
instruction + response. For classification, use text + label. For code, use prompt + code + explanation.
Always infer from context. Never add fields the user didn't ask for.

BLUEPRINT: This is the most important output. Write it as if briefing a skilled contractor who
has never seen the user's prompt. Be specific. Include: what good looks like, what bad looks like,
what the verifier should flag, how to handle ambiguous cases.

PIPELINE MODE: Set automatically based on context:
  - "file" if analyser_context is provided (files were attached)
  - "research" if the topic requires current/live information
  - "edit" if the user wants to modify an existing dataset
  - "minimal" if the request is very simple (< 3 fields, 1 category, clear spec)
  - "vibe" otherwise

FEASIBILITY: Mark feasible=false if: the request is illegal, impossible, contradictory,
or would require information you cannot provide.
"""


# ─── Interpreter agent ────────────────────────────────────────────────────────


class Interpreter:

    def __init__(self, router: LiteLLMRouter, emitter: Emitter):
        self.router  = router
        self.emitter = emitter

    async def run(
        self,
        config: JobConfig,
        analyser_context: Optional[str] = None,
    ) -> GenerationSpec:
        """
        Run the Interpreter on the job config.

        Args:
            config:           The full JobConfig from the user.
            analyser_context: Pre-processed context from Analyser (if files attached).

        Returns:
            GenerationSpec — the structured plan for the rest of the pipeline.
        """

        self.emitter.agent_status(AgentID.INTERPRETER, AgentStatus.RUNNING, current_task="Parsing prompt")
        self.emitter.log(AgentID.INTERPRETER, f"Interpreting: '{config.prompt[:80]}{'...' if len(config.prompt)>80 else ''}'")

        # Build the user message
        user_content = self._build_user_message(config, analyser_context)

        try:
            raw, model = await self.router.complete_json(
                agent_id=AgentID.INTERPRETER,
                messages=[{"role": "user", "content": user_content}],
                system=SYSTEM_PROMPT,
                temperature=0.3,   # Low temp — we want deterministic structured output
                max_tokens=4096,
            )
        except Exception as e:
            self.emitter.agent_status(AgentID.INTERPRETER, AgentStatus.FAILED)
            self.emitter.error("Interpreter failed", str(e))
            raise

        # Validate and normalise
        spec = self._validate_and_normalise(raw, config)

        self.emitter.log(
            AgentID.INTERPRETER,
            f"Spec ready. Mode: {spec.pipeline_mode.value} · "
            f"{len(spec.categories)} categories · "
            f"{spec.total_rows} rows · "
            f"{len(spec.fields)} fields. "
            f"{'Researcher needed.' if spec.needs_researcher else ''}"
        )

        if spec.warnings:
            for w in spec.warnings:
                self.emitter.warning("Interpreter warning", w)

        if not spec.feasible:
            self.emitter.error("Job not feasible", spec.infeasibility_reason)
            raise ValueError(f"Interpreter marked job infeasible: {spec.infeasibility_reason}")

        self.emitter.agent_status(
            AgentID.INTERPRETER,
            AgentStatus.DONE,
            model_used=model,
            current_task="Spec complete",
        )

        return spec

    def _build_user_message(self, config: JobConfig, analyser_context: Optional[str]) -> str:
        parts = []

        parts.append(f"## User Request\n{config.prompt}")

        if config.extra_context:
            parts.append(f"## Extra Context\n{config.extra_context}")

        if config.negative_prompt:
            parts.append(f"## Negative Prompting (what to avoid)\n{config.negative_prompt}")

        if analyser_context:
            parts.append(f"## Analyser Context (from attached files)\n{analyser_context}")

        if config.attached_files:
            parts.append("## Attached Files\n" + "\n".join(f"- {f}" for f in config.attached_files))

        parts.append("## Job Configuration")
        parts.append(f"- Output format: {config.output_format.value.upper()}")
        parts.append(f"- Use case: {config.use_case or 'not specified'}")
        parts.append(f"- Total rows: {config.total_rows}")
        parts.append(f"- Language: {config.language}")
        parts.append(f"- Seed examples: {config.seed_count} (max 150)")
        parts.append(f"- Diversity level: {config.diversity_level}/5")
        parts.append(f"- Edge case coverage: {config.edge_case_coverage}")

        if config.category_count:
            parts.append(f"- Category count: {config.category_count} (user-specified)")

        if config.field_constraints:
            parts.append("\n## User-Defined Field Constraints")
            for fc in config.field_constraints:
                parts.append(
                    f"- Field '{fc.field}': "
                    f"{'required' if fc.required else 'optional'}, "
                    f"min_length={fc.min_length}, max_length={fc.max_length}. "
                    f"{fc.description}"
                )

        parts.append(
            "\nNow produce the full GenerationSpec JSON. "
            "Be thorough with the blueprint — it drives all quality decisions downstream."
        )

        return "\n\n".join(parts)

    def _validate_and_normalise(self, raw: dict, config: JobConfig) -> GenerationSpec:
        """Apply config overrides and sanity-check the spec."""

        # Respect user's explicit row count
        raw["total_rows"] = config.total_rows

        # Respect user's explicit category count if set
        if config.category_count and isinstance(raw.get("categories"), list):
            cats = raw["categories"]
            if len(cats) != config.category_count:
                # Trim or note — Interpreter may have ignored it
                log.warning(
                    f"User requested {config.category_count} categories, "
                    f"Interpreter produced {len(cats)}. Using Interpreter's categories."
                )

        # Ensure category_targets sums to total_rows, minimum 1 per category
        if raw.get("category_targets") and raw.get("categories"):
            targets: dict = raw["category_targets"]
            cats: list    = raw["categories"]
            total = sum(targets.values())
            if total != config.total_rows and total > 0:
                factor = config.total_rows / total
                for k in targets:
                    targets[k] = max(1, round(targets[k] * factor))
            # Fix rounding — adjust largest to hit exact total
            current = sum(targets.values())
            diff    = config.total_rows - current
            if diff != 0 and targets:
                largest = max(targets, key=targets.get)
                targets[largest] = max(1, targets[largest] + diff)
            # Ensure every category has at least 1 row
            for cat in cats:
                if cat not in targets or targets[cat] < 1:
                    targets[cat] = 1

        # Apply user field constraints on top of Interpreter's fields
        if config.field_constraints:
            existing_names = {f["name"] for f in raw.get("fields", [])}
            for fc in config.field_constraints:
                if fc.field not in existing_names:
                    raw.setdefault("fields", []).append({
                        "name": fc.field,
                        "description": fc.description,
                        "required": fc.required,
                        "min_length": fc.min_length,
                        "max_length": fc.max_length,
                        "examples": fc.examples,
                        "forbidden_patterns": fc.forbidden_patterns,
                    })
                else:
                    # Merge — user constraints take priority
                    for f in raw["fields"]:
                        if f["name"] == fc.field:
                            if fc.min_length: f["min_length"] = fc.min_length
                            if fc.max_length: f["max_length"] = fc.max_length
                            if fc.forbidden_patterns: f["forbidden_patterns"] = fc.forbidden_patterns
                            if fc.description: f["description"] = fc.description

        # Language
        raw["language"] = config.language or raw.get("language", "English")

        # Pipeline mode — file takes precedence if files are attached
        if config.attached_files:
            raw["pipeline_mode"] = PipelineMode.FILE.value

        return GenerationSpec(raw)

    def spec_to_field_constraints(self, spec: GenerationSpec) -> List[FieldConstraint]:
        """Convert GenerationSpec fields to FieldConstraint objects for Linter/Verifier."""
        constraints = []
        for f in spec.fields:
            constraints.append(FieldConstraint(
                field=f["name"],
                description=f.get("description", ""),
                required=f.get("required", True),
                min_length=f.get("min_length"),
                max_length=f.get("max_length"),
                examples=f.get("examples", []),
                forbidden_patterns=f.get("forbidden_patterns", []),
            ))
        return constraints
