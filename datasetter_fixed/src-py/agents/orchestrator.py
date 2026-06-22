"""
agents/orchestrator.py

The Orchestrator is the brain of the pipeline.
It coordinates all agents, manages the state machine, handles failures,
decides which agents to activate, and emits progress events.

The Orchestrator does NOT generate, verify, or fix data.
It does: routing, sequencing, error recovery, pause/resume, and
deciding when to loop (Fixer → Verifier → Fixer cycles).

Model: the most capable available (Claude Sonnet, GPT-4.1, etc.)
It needs to reason about failures, make routing decisions under uncertainty,
and handle the infinite ways a pipeline can go wrong.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import List, Optional
from uuid import UUID

from agents.analyser import Analyser
from agents.fixer import Fixer
from agents.generator import Generator
from agents.interpreter import GenerationSpec, Interpreter
from agents.researcher import Researcher
from agents.scripter import Scripter
from agents.verifier import Verifier
from core.litellm_router import LiteLLMRouter
from core.models import (
    AgentID,
    AgentState,
    AgentStatus,
    DatasetRow,
    JobConfig,
    PipelineMode,
    PipelineState,
    PipelineStatus,
    RowStatus,
    SeedPack,
)
from core.optimizations import TokenTracker
from utils.events import Emitter
from utils.storage import (
    save_config,
    save_seeds,
    save_state,
)

log = logging.getLogger(__name__)


class PipelineContext:
    """Mutable state carried through the pipeline run."""
    def __init__(self, config: JobConfig):
        self.config:   JobConfig            = config
        self.spec:     Optional[GenerationSpec] = None
        self.seeds:    Optional[SeedPack]   = None
        self.rows:     List[DatasetRow]     = []
        self.paused:   bool                 = False
        self.cancelled: bool                = False


class Orchestrator:

    def __init__(
        self,
        router: LiteLLMRouter,
        emitter: Emitter,
    ):
        self.router  = router
        self.emitter = emitter

        # Instantiate all agents
        self.analyser   = Analyser(router, emitter)
        self.interpreter = Interpreter(router, emitter)
        self.researcher = Researcher(router, emitter, google_api_key=None)  # set from config at run time
        self.generator  = Generator(router, emitter)
        self.scripter   = Scripter(router, emitter)
        self.verifier   = Verifier(router, emitter)
        self.fixer      = Fixer(router, emitter)

        # Pipeline state for pause/resume/cancel
        self._contexts: dict[UUID, PipelineContext] = {}

    def pause(self, job_id: UUID) -> None:
        ctx = self._contexts.get(job_id)
        if ctx:
            ctx.paused = True
            log.info(f"Pipeline {job_id} pause requested.")

    def resume(self, job_id: UUID) -> None:
        ctx = self._contexts.get(job_id)
        if ctx:
            ctx.paused = False
            log.info(f"Pipeline {job_id} resumed.")

    def cancel(self, job_id: UUID) -> None:
        ctx = self._contexts.get(job_id)
        if ctx:
            ctx.cancelled = True
            log.info(f"Pipeline {job_id} cancel requested.")

    async def _check_pause(self, ctx: PipelineContext) -> bool:
        """
        Wait while paused. Returns True if should continue, False if cancelled.
        Emits a paused state update on entry.
        """
        if ctx.paused:
            self.emitter.log(AgentID.ORCHESTRATOR, "Pipeline paused.")
        while ctx.paused and not ctx.cancelled:
            await asyncio.sleep(0.5)
        if ctx.cancelled:
            self.emitter.log(AgentID.ORCHESTRATOR, "Pipeline cancelled.")
            return False
        return True

    async def run(self, config: JobConfig) -> List[DatasetRow]:
        """
        Execute the full pipeline for a job.
        A fresh TokenTracker is attached to the router for this run
        so token usage is recorded across all agent calls.
        """
        ctx = PipelineContext(config)
        self._contexts[config.job_id] = ctx

        # Attach a fresh token tracker to the router for this job
        self.router.token_tracker = TokenTracker()

        # Set google API key on researcher
        if self.router.api_keys.google:
            self.researcher.google_api_key = self.router.api_keys.google

        # Save config to disk immediately
        await save_config(config)

        state = self._make_state(config, PipelineStatus.RUNNING)
        await self._emit_state(state)

        self.emitter.log(
            AgentID.ORCHESTRATOR,
            f"Pipeline started. Job: '{config.name}' · {config.total_rows} rows · "
            f"{config.output_format.value.upper()}.",
        )

        try:
            rows = await self._execute(ctx, state)
            state.status = PipelineStatus.COMPLETE
            await self._emit_state(state)

            ok_count = sum(1 for r in rows if r.status == RowStatus.OK)

            # Log token usage summary
            token_summary = self.router.token_tracker.summary()
            cache_rate    = self.router.token_tracker.cache_hit_rate()
            self.emitter.log(AgentID.ORCHESTRATOR, f"Token usage — {token_summary}")
            if cache_rate > 0:
                self.emitter.log(
                    AgentID.ORCHESTRATOR,
                    f"Prompt cache hit rate: {cache_rate:.0%} "
                    f"(cached tokens cost ~10% of normal input price on Anthropic).",
                )

            self.emitter.success(
                "Pipeline complete",
                f"{ok_count}/{config.total_rows} rows verified and ready to export.",
            )
            return rows

        except asyncio.CancelledError:
            state.status = PipelineStatus.CANCELLED
            await self._emit_state(state)
            return ctx.rows

        except Exception as e:
            log.exception(f"Pipeline {config.job_id} failed: {e}")
            state.status = PipelineStatus.FAILED
            await self._emit_state(state)
            self.emitter.error("Pipeline failed", str(e))
            raise

        finally:
            self._contexts.pop(config.job_id, None)

    async def _execute(self, ctx: PipelineContext, state: PipelineState) -> List[DatasetRow]:
        config = ctx.config

        # ── Step 1: Analyser (if files attached) ─────────────────────────────
        analyser_context: Optional[str] = None

        if config.attached_files:
            if not await self._check_pause(ctx): return ctx.rows
            state = await self._update_agent(state, AgentID.ANALYSER, AgentStatus.RUNNING)

            try:
                analyser_context = await self.analyser.run(config)
                state = await self._update_agent(state, AgentID.ANALYSER, AgentStatus.DONE)
            except Exception as e:
                self.emitter.warning("Analyser", f"Failed: {e}. Continuing without file context.")
                state = await self._update_agent(state, AgentID.ANALYSER, AgentStatus.FAILED)
                # Non-fatal — pipeline continues without analyser context

        # ── Step 2: Interpreter ───────────────────────────────────────────────
        if not await self._check_pause(ctx): return ctx.rows
        state = await self._update_agent(state, AgentID.INTERPRETER, AgentStatus.RUNNING)

        spec = await self._run_with_retry(
            fn=lambda: self.interpreter.run(config, analyser_context),
            agent_id=AgentID.INTERPRETER,
            max_retries=3,
            state=state,
        )

        if spec is None:
            raise RuntimeError("Interpreter failed after retries. Cannot continue.")

        ctx.spec = spec
        state.pipeline_mode = spec.pipeline_mode
        state = await self._update_agent(state, AgentID.INTERPRETER, AgentStatus.DONE)

        # Update field constraints from spec if user didn't specify them
        if not config.field_constraints:
            config.field_constraints = self.interpreter.spec_to_field_constraints(spec)

        # ── Step 3: Researcher (if needed) ────────────────────────────────────
        research_context: Optional[str] = None

        if spec.needs_researcher and spec.researcher_queries:
            if not await self._check_pause(ctx): return ctx.rows
            state = await self._update_agent(state, AgentID.RESEARCHER, AgentStatus.RUNNING)

            try:
                research_context = await self.researcher.run(spec.researcher_queries, config)
                state = await self._update_agent(state, AgentID.RESEARCHER, AgentStatus.DONE)
            except Exception as e:
                self.emitter.warning("Researcher", f"Failed: {e}. Continuing without research.")
                state = await self._update_agent(state, AgentID.RESEARCHER, AgentStatus.FAILED)

        # ── Step 4: Generator ─────────────────────────────────────────────────
        # Skip Generator for EDIT or MINIMAL pipelines
        skip_generator = spec.pipeline_mode in (PipelineMode.EDIT, PipelineMode.MINIMAL)

        if not skip_generator:
            if not await self._check_pause(ctx): return ctx.rows
            state = await self._update_agent(state, AgentID.GENERATOR, AgentStatus.RUNNING)

            # Capture spec/research_context by value to avoid closure hazard
            _spec             = spec
            _research_context = research_context
            seed_pack = await self._run_with_retry(
                fn=lambda: self.generator.run(config, _spec, _research_context),
                agent_id=AgentID.GENERATOR,
                max_retries=3,
                state=state,
            )

            if seed_pack is None:
                raise RuntimeError("Generator failed after retries. Cannot continue.")

            ctx.seeds = seed_pack
            await save_seeds(config.job_id, seed_pack)
            state = await self._update_agent(state, AgentID.GENERATOR, AgentStatus.DONE)

            self.emitter.log(
                AgentID.ORCHESTRATOR,
                f"Seed pack ready. {len(seed_pack.seeds)} seeds. Routing to Scripter."
            )
        else:
            ctx.seeds = self._minimal_seed_pack(spec, config)
            self.emitter.log(AgentID.ORCHESTRATOR, "Minimal/edit pipeline: skipping Generator.")

        # ── Step 5: Scripter ──────────────────────────────────────────────────
        if ctx.seeds is None:
            raise RuntimeError("No seed pack available. Cannot run Scripter.")

        if not await self._check_pause(ctx): return ctx.rows
        state = await self._update_agent(state, AgentID.SCRIPTER, AgentStatus.RUNNING)
        state.total_rows = config.total_rows

        _seeds = ctx.seeds  # capture by value
        rows = await self._run_with_retry(
            fn=lambda: self.scripter.run(config, _seeds),
            agent_id=AgentID.SCRIPTER,
            max_retries=2,
            state=state,
        )

        if rows is None:
            raise RuntimeError("Scripter failed after retries.")

        ctx.rows = rows
        state.generated_rows = len(rows)
        state = await self._update_agent(state, AgentID.SCRIPTER, AgentStatus.DONE)

        self.emitter.info(
            "Generation complete",
            f"{len(rows)} rows generated. Starting verification."
        )

        # ── Step 6: Verifier + Fixer loop ─────────────────────────────────────
        if not await self._check_pause(ctx): return ctx.rows
        state = await self._update_agent(state, AgentID.VERIFIER, AgentStatus.RUNNING)

        rows = await self.verifier.run(rows, config, ctx.seeds)
        ctx.rows = rows

        error_rows = [r for r in rows if r.status == RowStatus.ERROR]
        state.error_rows = len(error_rows)
        state.verified_rows = sum(1 for r in rows if r.status == RowStatus.OK)

        # Fix errors if auto_fix enabled
        if error_rows and config.auto_fix:
            if not await self._check_pause(ctx): return ctx.rows
            state = await self._update_agent(state, AgentID.FIXER, AgentStatus.RUNNING)

            self.emitter.log(
                AgentID.ORCHESTRATOR,
                f"{len(error_rows)} rows need fixing. Starting Fixer."
            )

            fixed_rows = await self.fixer.run(
                error_rows=error_rows,
                config=config,
                seed_pack=ctx.seeds,
                verifier=self.verifier,
            )

            # Merge fixed rows back into full rows list
            fixed_map = {r.id: r for r in fixed_rows}
            ctx.rows = [fixed_map.get(r.id, r) for r in ctx.rows]

            state = await self._update_agent(state, AgentID.FIXER, AgentStatus.DONE)
            state = await self._update_agent(state, AgentID.VERIFIER, AgentStatus.DONE)

            still_broken = sum(1 for r in ctx.rows if r.status == RowStatus.ERROR)
            state.error_rows  = still_broken
            state.fixed_rows  = len(error_rows) - still_broken
            state.verified_rows = sum(1 for r in ctx.rows if r.status == RowStatus.OK)

            self.emitter.log(
                AgentID.ORCHESTRATOR,
                f"Fix cycle complete. "
                f"{state.verified_rows} verified, {state.error_rows} still need review."
            )
        else:
            state = await self._update_agent(state, AgentID.VERIFIER, AgentStatus.DONE)

        # ── Final state ───────────────────────────────────────────────────────
        state.verified_rows = sum(1 for r in ctx.rows if r.status == RowStatus.OK)
        state.error_rows    = sum(1 for r in ctx.rows if r.status == RowStatus.ERROR)
        await self._emit_state(state)  # saves state internally

        return ctx.rows

    # ── Helpers ───────────────────────────────────────────────────────────────

    async def _run_with_retry(self, fn, agent_id: AgentID, max_retries: int, state: PipelineState):
        """Run an agent function with automatic retries. Returns result or None."""
        for attempt in range(1, max_retries + 1):
            try:
                return await fn()
            except Exception as e:
                log.error(f"{agent_id} attempt {attempt}/{max_retries} failed: {e}")
                if attempt < max_retries:
                    wait = 2 ** attempt
                    self.emitter.log(
                        AgentID.ORCHESTRATOR,
                        f"{agent_id} failed (attempt {attempt}). Retrying in {wait}s. Error: {e}"
                    )
                    await asyncio.sleep(wait)
                else:
                    self.emitter.error(
                        f"{agent_id} failed",
                        f"All {max_retries} attempts failed. Last error: {e}"
                    )
        return None

    def _make_state(self, config: JobConfig, status: PipelineStatus) -> PipelineState:
        agent_states = {
            a: AgentState(agent_id=a, status=AgentStatus.IDLE)
            for a in AgentID
        }
        return PipelineState(
            job_id=config.job_id,
            status=status,
            total_rows=config.total_rows,
            agent_states=agent_states,
            started_at=time.time(),
        )

    async def _update_agent(
        self,
        state: PipelineState,
        agent_id: AgentID,
        status: AgentStatus,
    ) -> PipelineState:
        if agent_id in state.agent_states:
            state.agent_states[agent_id].status = status
            if status == AgentStatus.RUNNING:
                state.agent_states[agent_id].started_at = time.time()
            elif status in (AgentStatus.DONE, AgentStatus.FAILED):
                state.agent_states[agent_id].finished_at = time.time()
        await self._emit_state(state)
        return state

    async def _emit_state(self, state: PipelineState) -> None:
        self.emitter.pipeline_state(state)
        await save_state(state)

    def _minimal_seed_pack(self, spec: GenerationSpec, config: JobConfig) -> SeedPack:
        """Build a minimal SeedPack from spec alone (no Generator needed)."""
        targets = spec.category_targets or {
            cat: config.total_rows // max(len(spec.categories), 1)
            for cat in spec.categories
        }
        return SeedPack(
            seeds=[],
            categories=spec.categories,
            category_targets=targets,
            generation_spec=spec.to_dict(),
            blueprint=spec.blueprint,
        )
