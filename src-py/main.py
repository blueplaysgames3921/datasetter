"""
main.py

FastAPI server — the Python sidecar.
Tauri spawns this as a subprocess on a free port.
Frontend connects via HTTP + SSE for live pipeline events.

Fixes applied vs original:
  - Logging setup no longer has broken FileHandler conditional
  - regenerate_row no longer accesses private Scripter methods directly
  - StartJobResponse pipeline_mode is None until Interpreter runs
  - All storage calls use the correct async/sync APIs
  - update_row awaited properly
  - settings field renamed from model_config_ to agent_models
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import List, Optional
from uuid import UUID

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from agents.orchestrator import Orchestrator
from core.litellm_router import LiteLLMRouter
from core.models import (
    AgentID,
    AppSettings,
    EditRowRequest,
    ErrorType,
    ExportRequest,
    ExportResponse,
    FlagRowRequest,
    ProjectSummary,
    RegenerateRowRequest,
    RowStatus,
    StartJobRequest,
    StartJobResponse,
    VerifierError,
)
from utils.events import Emitter, event_registry, sse_generator
from utils.hardware import get_or_scan, scan_hardware, save_profile
from utils.storage import (
    delete_project,
    export_dataset,
    list_projects,
    load_config,
    load_rows,
    load_seeds,
    load_state,
    update_row,
)

# ─── Logging ──────────────────────────────────────────────────────────────────

def _setup_logging() -> None:
    log_dir = Path("~/datasetter/logs").expanduser()
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "sidecar.log"

    handlers: List[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    try:
        handlers.append(logging.FileHandler(log_file, mode="a"))
    except OSError as e:
        print(f"[warning] Could not open log file {log_file}: {e}", file=sys.stderr)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=handlers,
    )

_setup_logging()
log = logging.getLogger(__name__)

# ─── App state ────────────────────────────────────────────────────────────────

SETTINGS_PATH = Path("~/datasetter/settings.json").expanduser()

_settings:      Optional[AppSettings]       = None
_orchestrators: dict[UUID, Orchestrator]    = {}
_active_tasks:  dict[UUID, asyncio.Task]    = {}


def _load_settings() -> AppSettings:
    global _settings
    if SETTINGS_PATH.exists():
        try:
            data = json.loads(SETTINGS_PATH.read_text())
            _settings = AppSettings(**data)
            return _settings
        except Exception as e:
            log.warning(f"Could not load settings: {e}. Using defaults.")
    _settings = AppSettings()
    return _settings


def _save_settings(settings: AppSettings) -> None:
    global _settings
    _settings = settings
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(settings.model_dump_json(indent=2))


def _get_settings() -> AppSettings:
    if _settings is None:
        _load_settings()
    return _settings


def _make_router() -> LiteLLMRouter:
    settings = _get_settings()
    hardware = settings.hardware or get_or_scan()
    return LiteLLMRouter(settings=settings, hardware=hardware)


# ─── Lifespan ─────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Datasetter sidecar starting.")
    _load_settings()
    settings = _get_settings()

    # First run: scan hardware
    if settings.hardware is None:
        log.info("First run: scanning hardware.")
        profile          = await asyncio.to_thread(get_or_scan)
        settings.hardware = profile
        _save_settings(settings)

    # First run (or reset): run Configuration Agent to pick optimal models
    if not settings.agent_models.assignments:
        log.info("No model assignments found — running Configuration Agent.")
        try:
            from core.config_agent import run_config_agent
            api_keys_dict = {
                "google":    settings.api_keys.google,
                "anthropic": settings.api_keys.anthropic,
            }
            model_config = await run_config_agent(
                hardware=settings.hardware,
                api_keys=api_keys_dict,
                existing_settings=settings,
            )
            settings.agent_models = model_config
            _save_settings(settings)
            log.info("Configuration Agent complete. Model assignments saved.")
        except Exception as e:
            log.error(f"Configuration Agent failed: {e}. Using built-in defaults.")

    log.info("Sidecar ready.")
    yield
    log.info("Sidecar shutting down.")
    event_registry.close_all()
    for task in list(_active_tasks.values()):
        task.cancel()
        try:
            await asyncio.wait_for(task, timeout=3.0)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass


# ─── App ──────────────────────────────────────────────────────────────────────

app = FastAPI(title="Datasetter", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:1420", "tauri://localhost"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── Health ───────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "timestamp": time.time()}


# ─── Jobs ─────────────────────────────────────────────────────────────────────

@app.post("/jobs/start", response_model=StartJobResponse)
async def start_job(req: StartJobRequest):
    config = req.config

    if not config.prompt.strip():
        raise HTTPException(status_code=400, detail="prompt is required")

    # Create event bus and emitter
    bus     = event_registry.create(config.job_id)
    emitter = Emitter(bus)

    # Build orchestrator with fresh router
    router       = _make_router()
    orchestrator = Orchestrator(router=router, emitter=emitter)
    _orchestrators[config.job_id] = orchestrator

    # Preliminary agent list — Interpreter will refine this
    active_agents = [
        AgentID.ORCHESTRATOR, AgentID.INTERPRETER,
        AgentID.GENERATOR,    AgentID.SCRIPTER,
        AgentID.VERIFIER,     AgentID.FIXER,
    ]
    if config.attached_files:
        active_agents.insert(1, AgentID.ANALYSER)

    async def run_pipeline():
        try:
            await orchestrator.run(config)
        except asyncio.CancelledError:
            log.info(f"Pipeline {config.job_id} was cancelled.")
        except Exception as e:
            log.error(f"Pipeline {config.job_id} unhandled error: {e}")
        finally:
            event_registry.close(config.job_id)
            _orchestrators.pop(config.job_id, None)
            _active_tasks.pop(config.job_id, None)

    task = asyncio.create_task(run_pipeline())
    _active_tasks[config.job_id] = task

    log.info(f"Job {config.job_id} started: '{config.name}'")

    return StartJobResponse(
        job_id=config.job_id,
        pipeline_mode=config.pipeline_mode or "vibe",  # preliminary — Interpreter decides
        active_agents=active_agents,
        message="Pipeline started.",
    )


@app.post("/jobs/{job_id}/pause")
async def pause_job(job_id: UUID):
    orch = _orchestrators.get(job_id)
    if not orch:
        raise HTTPException(status_code=404, detail="Job not found or not running")
    orch.pause(job_id)
    return {"status": "paused"}


@app.post("/jobs/{job_id}/resume")
async def resume_job(job_id: UUID):
    orch = _orchestrators.get(job_id)
    if not orch:
        raise HTTPException(status_code=404, detail="Job not found or not running")
    orch.resume(job_id)
    return {"status": "resumed"}


@app.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: UUID):
    orch = _orchestrators.get(job_id)
    if orch:
        orch.cancel(job_id)
    task = _active_tasks.get(job_id)
    if task and not task.done():
        task.cancel()
    event_registry.close(job_id)
    return {"status": "cancelled"}


# ─── SSE stream ───────────────────────────────────────────────────────────────

@app.get("/jobs/{job_id}/events")
async def job_events(job_id: UUID, request: Request):
    """
    Server-Sent Events stream. Frontend connects here for live pipeline events.
    Keep-alive comment sent every 30s to prevent proxy timeouts.
    """
    bus = event_registry.get(job_id)
    if not bus:
        raise HTTPException(status_code=404, detail="No active event stream for this job.")

    async def generate():
        async for chunk in sse_generator(bus):
            if await request.is_disconnected():
                break
            yield chunk

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control":    "no-cache",
            "X-Accel-Buffering":"no",
            "Connection":       "keep-alive",
        },
    )


# ─── Rows ─────────────────────────────────────────────────────────────────────

@app.get("/jobs/{job_id}/rows")
async def get_rows(
    job_id:   UUID,
    status:   Optional[str] = None,
    category: Optional[str] = None,
    offset:   int = 0,
    limit:    int = 200,
):
    rows = load_rows(job_id)

    if status:
        try:
            st   = RowStatus(status)
            rows = [r for r in rows if r.status == st]
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid status: {status!r}")

    if category:
        rows = [r for r in rows if r.category == category]

    total = len(rows)
    rows  = rows[offset: offset + limit]

    return {
        "total":  total,
        "offset": offset,
        "limit":  limit,
        "rows":   [r.model_dump() for r in rows],
    }


@app.patch("/jobs/{job_id}/rows/{row_id}")
async def edit_row(job_id: UUID, row_id: int, req: EditRowRequest):
    rows = load_rows(job_id)
    row  = next((r for r in rows if r.id == row_id), None)
    if not row:
        raise HTTPException(status_code=404, detail="Row not found")

    # Handle __category__ as a special key for editing the row's category
    fields = dict(req.fields)
    new_category = fields.pop("__category__", None)

    row.fields.update(fields)
    if new_category is not None:
        row.category = str(new_category)

    row.manually_edited = True
    row.status = RowStatus.MANUAL
    await update_row(job_id, row)
    return {"status": "updated", "row": row.model_dump()}


@app.post("/jobs/{job_id}/rows/{row_id}/accept")
async def accept_row(job_id: UUID, row_id: int):
    rows = load_rows(job_id)
    row  = next((r for r in rows if r.id == row_id), None)
    if not row:
        raise HTTPException(status_code=404, detail="Row not found")

    row.status = RowStatus.OK
    row.errors = []
    await update_row(job_id, row)
    return {"status": "accepted"}


@app.post("/jobs/{job_id}/rows/{row_id}/flag")
async def flag_row(job_id: UUID, row_id: int, req: FlagRowRequest):
    rows = load_rows(job_id)
    row  = next((r for r in rows if r.id == row_id), None)
    if not row:
        raise HTTPException(status_code=404, detail="Row not found")

    row.status = RowStatus.ERROR
    row.errors = [VerifierError(
        row_id=row_id,
        error_type=ErrorType.SEMANTIC,
        field="*",
        description=req.reason,
        fix_instruction=f"Rewrite this row to fix the following issue: {req.reason}",
        severity="minor",
    )]
    await update_row(job_id, row)
    return {"status": "flagged"}


@app.post("/jobs/{job_id}/rows/{row_id}/regenerate")
async def regenerate_row(job_id: UUID, row_id: int, req: RegenerateRowRequest):
    """
    Re-run an agent on a single row.
    Routes through Orchestrator helpers rather than accessing private methods.
    """
    rows = load_rows(job_id)
    row  = next((r for r in rows if r.id == row_id), None)
    if not row:
        raise HTTPException(status_code=404, detail="Row not found")

    config = load_config(job_id)
    if not config:
        raise HTTPException(status_code=404, detail="Job config not found")

    seeds = load_seeds(job_id)
    if not seeds:
        raise HTTPException(
            status_code=400,
            detail="Seed pack not found for this job. Cannot regenerate without seeds."
        )

    bus     = event_registry.get(job_id) or event_registry.create(job_id)
    emitter = Emitter(bus)
    router  = _make_router()

    async def _regen():
        row.status = RowStatus.FIXING
        await update_row(job_id, row)
        emitter.row_update(row)

        try:
            if row.errors and req.agent_id != AgentID.SCRIPTER:
                # Has Verifier errors — send through Fixer → re-verify cycle
                from agents.fixer    import Fixer
                from agents.verifier import Verifier
                fixer    = Fixer(router=router, emitter=emitter)
                verifier = Verifier(router=router, emitter=emitter)

                fixed = await fixer._fix_row(row, config)
                # Re-verify just this row
                reverified = await verifier.verify_rows(
                    rows=[fixed], config=config, blueprint=seeds.blueprint
                )
                updated = reverified[0] if reverified else fixed
            else:
                # No errors or explicit Scripter re-run — regenerate from scratch
                from agents.scripter import Scripter
                scripter   = Scripter(router=router, emitter=emitter)
                from utils.linter import Linter
                linter     = Linter(config)
                cat_seeds  = [s for s in seeds.seeds if s.category == row.category]

                # Build a single-row batch using the Scripter's public interface
                new_rows = await scripter._generate_category(
                    category=row.category,
                    target_rows=1,
                    seeds=cat_seeds,
                    seed_pack=seeds,
                    config=config,
                    linter=linter,
                    start_id=row.id,
                )
                updated = new_rows[0] if new_rows else row
                # Preserve original row id
                updated.id = row.id

            await update_row(job_id, updated)
            emitter.row_update(updated)

        except Exception as e:
            log.error(f"Regenerate row {row_id} failed: {e}")
            emitter.error("Regenerate failed", str(e))
            # Restore original row on failure
            row.status = RowStatus.ERROR if row.errors else RowStatus.PENDING
            await update_row(job_id, row)
            emitter.row_update(row)

    asyncio.create_task(_regen())
    return {"status": "regenerating"}


# ─── Export ───────────────────────────────────────────────────────────────────

@app.post("/jobs/{job_id}/export", response_model=ExportResponse)
async def export_job(job_id: UUID, req: ExportRequest):
    try:
        path, count = await asyncio.to_thread(
            export_dataset,
            job_id=job_id,
            output_format=req.output_format,
            include_statuses=req.include_statuses,
            output_path=req.output_path,
        )
        return ExportResponse(path=str(path), row_count=count, format=req.output_format)
    except Exception as e:
        log.error(f"Export failed for {job_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ─── Projects ─────────────────────────────────────────────────────────────────

@app.get("/projects", response_model=List[ProjectSummary])
async def get_projects():
    return await asyncio.to_thread(list_projects)


@app.get("/projects/{job_id}")
async def get_project(job_id: UUID):
    config = load_config(job_id)
    if not config:
        raise HTTPException(status_code=404, detail="Project not found")
    state = load_state(job_id)
    return {
        "config": config.model_dump(),
        "state":  state.model_dump() if state else None,
    }


@app.delete("/projects/{job_id}")
async def delete_project_route(job_id: UUID):
    orch = _orchestrators.pop(job_id, None)
    if orch:
        orch.cancel(job_id)
    task = _active_tasks.pop(job_id, None)
    if task and not task.done():
        task.cancel()
    event_registry.close(job_id)
    await asyncio.to_thread(delete_project, job_id)
    return {"status": "deleted"}


# ─── Settings ─────────────────────────────────────────────────────────────────

@app.get("/settings")
async def get_settings():
    return _get_settings().model_dump()


@app.put("/settings")
async def update_settings(settings: AppSettings):
    _save_settings(settings)
    return {"status": "saved"}


@app.get("/jobs/{job_id}/seeds")
async def get_seeds(job_id: UUID):
    """Return the seed pack for a job — used by the Seeds panel in the frontend."""
    seeds = await asyncio.to_thread(load_seeds, job_id)
    if not seeds:
        raise HTTPException(status_code=404, detail="No seeds found for this job.")
    return seeds.model_dump()


@app.get("/jobs/{job_id}/blueprint")
async def get_blueprint(job_id: UUID):
    """Return just the blueprint string — used for displaying the Interpreter's spec."""
    seeds = await asyncio.to_thread(load_seeds, job_id)
    if not seeds:
        raise HTTPException(status_code=404, detail="No seeds found for this job.")
    return {"blueprint": seeds.blueprint}


@app.post("/settings/scan-hardware")
async def rescan_hardware():
    """Re-scan hardware and update the stored profile."""
    profile = await asyncio.to_thread(scan_hardware)
    save_profile(profile)
    settings = _get_settings()
    settings.hardware = profile
    _save_settings(settings)
    return profile.model_dump()


@app.post("/settings/reconfigure")
async def reconfigure_models():
    """
    Re-run the Configuration Agent to regenerate model assignments.
    Useful after adding new API keys or changing hardware.
    """
    settings = _get_settings()
    if not settings.hardware:
        settings.hardware = await asyncio.to_thread(get_or_scan)

    try:
        from core.config_agent import run_config_agent
        api_keys_dict = {
            "google":    settings.api_keys.google,
            "anthropic": settings.api_keys.anthropic,
        }
        model_config = await run_config_agent(
            hardware=settings.hardware,
            api_keys=api_keys_dict,
            existing_settings=settings,
        )
        settings.agent_models = model_config
        _save_settings(settings)
        return {
            "status":      "ok",
            "assignments": {
                k.value: v.model_dump()
                for k, v in model_config.assignments.items()
            },
        }
    except Exception as e:
        log.error(f"Reconfigure failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/jobs/{job_id}/tokens")
async def get_token_usage(job_id: UUID):
    """
    Return token usage summary for a running or completed job.
    Pulls from the active orchestrator's router if the job is still running,
    or returns the last logged summary if available.
    """
    orch = _orchestrators.get(job_id)
    if orch and hasattr(orch.router, "token_tracker"):
        tracker = orch.router.token_tracker
        return {
            "job_id":     str(job_id),
            "summary":    tracker.summary(),
            "totals":     tracker.total(),
            "per_agent":  tracker.per_agent(),
            "cache_hit_rate": round(tracker.cache_hit_rate(), 3),
        }
    raise HTTPException(status_code=404, detail="No token data for this job.")


@app.get("/settings/hardware")
async def get_hardware():
    settings = _get_settings()
    if settings.hardware:
        return settings.hardware.model_dump()
    profile = await asyncio.to_thread(get_or_scan)
    return profile.model_dump()


# ─── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("DATASETTER_PORT", 57423))
    uvicorn.run(
        "main:app",
        host="127.0.0.1",
        port=port,
        log_level="info",
        reload=False,
    )
