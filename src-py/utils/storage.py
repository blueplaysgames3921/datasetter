"""
utils/storage.py

All dataset persistence lives here. Projects are stored as a directory per
job_id containing: job.json, state.json, rows.jsonl, seeds.json, exports/.

All IO runs via asyncio.to_thread — never blocks the event loop.
list_projects() reads only state.json per project (not all rows) — O(projects)
not O(projects * rows).
"""

from __future__ import annotations

import asyncio
import csv
import json
import logging
import time
from pathlib import Path
from typing import List, Optional
from uuid import UUID

from core.models import (
    DatasetRow,
    JobConfig,
    OutputFormat,
    PipelineState,
    ProjectSummary,
    RowStatus,
    SeedPack,
)

log = logging.getLogger(__name__)

DEFAULT_BASE = Path("~/datasetter").expanduser()


def _job_dir(job_id: UUID, base: Path = DEFAULT_BASE) -> Path:
    return base / "projects" / str(job_id)


def _ensure(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


# ─── Sync helpers (all run inside asyncio.to_thread) ──────────────────────────

def _sync_save_config(config: JobConfig, base: Path) -> None:
    d = _ensure(_job_dir(config.job_id, base))
    (d / "job.json").write_text(config.model_dump_json(indent=2))


def _sync_load_config(job_id: UUID, base: Path) -> Optional[JobConfig]:
    path = _job_dir(job_id, base) / "job.json"
    if not path.exists():
        return None
    try:
        return JobConfig(**json.loads(path.read_text()))
    except Exception as e:
        log.error(f"Failed to load config for {job_id}: {e}")
        return None


def _sync_save_state(state: PipelineState, base: Path) -> None:
    d = _ensure(_job_dir(state.job_id, base))
    (d / "state.json").write_text(state.model_dump_json(indent=2))


def _sync_load_state(job_id: UUID, base: Path) -> Optional[PipelineState]:
    path = _job_dir(job_id, base) / "state.json"
    if not path.exists():
        return None
    try:
        return PipelineState(**json.loads(path.read_text()))
    except Exception as e:
        log.error(f"Failed to load state for {job_id}: {e}")
        return None


def _sync_append_rows(job_id: UUID, rows: List[DatasetRow], base: Path) -> None:
    d = _ensure(_job_dir(job_id, base))
    with (d / "rows.jsonl").open("a", encoding="utf-8") as f:
        for row in rows:
            f.write(row.model_dump_json() + "\n")


def _sync_load_rows(job_id: UUID, base: Path) -> List[DatasetRow]:
    path = _job_dir(job_id, base) / "rows.jsonl"
    if not path.exists():
        return []
    rows: List[DatasetRow] = []
    with path.open("r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(DatasetRow(**json.loads(line)))
            except Exception as e:
                log.warning(f"Skipping malformed row at line {lineno} in {job_id}: {e}")
    return rows


def _sync_update_row(job_id: UUID, row: DatasetRow, base: Path) -> None:
    """
    Atomic row update: write to .tmp then rename over the original.
    Prevents corruption if the process dies mid-write.
    """
    rows  = _sync_load_rows(job_id, base)
    path  = _job_dir(job_id, base) / "rows.jsonl"
    tmp   = path.with_suffix(".jsonl.tmp")
    found = False

    with tmp.open("w", encoding="utf-8") as f:
        for r in rows:
            if r.id == row.id:
                f.write(row.model_dump_json() + "\n")
                found = True
            else:
                f.write(r.model_dump_json() + "\n")
        if not found:
            f.write(row.model_dump_json() + "\n")

    tmp.replace(path)


def _sync_save_seeds(job_id: UUID, seeds: SeedPack, base: Path) -> None:
    d = _ensure(_job_dir(job_id, base))
    (d / "seeds.json").write_text(seeds.model_dump_json(indent=2))


def _sync_load_seeds(job_id: UUID, base: Path) -> Optional[SeedPack]:
    path = _job_dir(job_id, base) / "seeds.json"
    if not path.exists():
        return None
    try:
        return SeedPack(**json.loads(path.read_text()))
    except Exception as e:
        log.error(f"Failed to load seeds for {job_id}: {e}")
        return None


# ─── Async public API ─────────────────────────────────────────────────────────

async def save_config(config: JobConfig, base: Path = DEFAULT_BASE) -> None:
    await asyncio.to_thread(_sync_save_config, config, base)


def load_config(job_id: UUID, base: Path = DEFAULT_BASE) -> Optional[JobConfig]:
    """Sync — safe to call from FastAPI route handlers."""
    return _sync_load_config(job_id, base)


async def async_load_config(job_id: UUID, base: Path = DEFAULT_BASE) -> Optional[JobConfig]:
    return await asyncio.to_thread(_sync_load_config, job_id, base)


async def save_state(state: PipelineState, base: Path = DEFAULT_BASE) -> None:
    await asyncio.to_thread(_sync_save_state, state, base)


def load_state(job_id: UUID, base: Path = DEFAULT_BASE) -> Optional[PipelineState]:
    """Sync — safe to call from FastAPI route handlers."""
    return _sync_load_state(job_id, base)


async def append_rows(job_id: UUID, rows: List[DatasetRow], base: Path = DEFAULT_BASE) -> None:
    if not rows:
        return
    await asyncio.to_thread(_sync_append_rows, job_id, rows, base)


def load_rows(job_id: UUID, base: Path = DEFAULT_BASE) -> List[DatasetRow]:
    """Sync — safe to call from FastAPI route handlers."""
    return _sync_load_rows(job_id, base)


async def update_row(job_id: UUID, row: DatasetRow, base: Path = DEFAULT_BASE) -> None:
    await asyncio.to_thread(_sync_update_row, job_id, row, base)


async def save_seeds(job_id: UUID, seeds: SeedPack, base: Path = DEFAULT_BASE) -> None:
    await asyncio.to_thread(_sync_save_seeds, job_id, seeds, base)


def load_seeds(job_id: UUID, base: Path = DEFAULT_BASE) -> Optional[SeedPack]:
    return _sync_load_seeds(job_id, base)


# ─── Export ───────────────────────────────────────────────────────────────────

def export_dataset(
    job_id: UUID,
    output_format: OutputFormat,
    include_statuses: Optional[List[RowStatus]] = None,
    output_path: Optional[str] = None,
    base: Path = DEFAULT_BASE,
) -> tuple[Path, int]:
    """Export verified rows. Returns (file_path, row_count). Sync."""
    if include_statuses is None:
        include_statuses = [RowStatus.OK]

    rows = _sync_load_rows(job_id, base)
    rows = [r for r in rows if r.status in include_statuses]

    if output_path:
        out = Path(output_path).expanduser()
    else:
        export_dir = _ensure(_job_dir(job_id, base) / "exports")
        out = export_dir / f"export_{int(time.time())}.{output_format.value}"

    out.parent.mkdir(parents=True, exist_ok=True)

    {
        OutputFormat.JSONL:   _export_jsonl,
        OutputFormat.JSON:    _export_json,
        OutputFormat.CSV:     _export_csv,
        OutputFormat.TSV:     lambda r, p: _export_csv(r, p, "\t"),
        OutputFormat.PARQUET: _export_parquet,
        OutputFormat.ARROW:   _export_arrow,
        OutputFormat.XLSX:    _export_xlsx,
        OutputFormat.XML:     _export_xml,
    }.get(output_format, _export_jsonl)(rows, out)

    log.info(f"Exported {len(rows)} rows → {out}")
    return out, len(rows)


def _export_jsonl(rows: List[DatasetRow], path: Path) -> None:
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row.fields, ensure_ascii=False) + "\n")


def _export_json(rows: List[DatasetRow], path: Path) -> None:
    path.write_text(
        json.dumps([r.fields for r in rows], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _export_csv(rows: List[DatasetRow], path: Path, delimiter: str = ",") -> None:
    if not rows:
        path.write_text("")
        return
    # Collect all unique field keys across every row (schema may vary row-to-row)
    seen: dict = {}
    for r in rows:
        for k in r.fields:
            seen[k] = None
    headers = list(seen)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=headers, delimiter=delimiter, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: str(v) for k, v in row.fields.items()})


def _export_parquet(rows: List[DatasetRow], path: Path) -> None:
    try:
        import pandas as pd
        pd.DataFrame([r.fields for r in rows]).to_parquet(path, index=False)
    except ImportError:
        log.warning("pandas/pyarrow not installed — falling back to JSONL")
        _export_jsonl(rows, path.with_suffix(".jsonl"))


def _export_arrow(rows: List[DatasetRow], path: Path) -> None:
    try:
        import pyarrow as pa, pyarrow.ipc as ipc
        if not rows:
            return
        table = pa.Table.from_pylist([r.fields for r in rows])
        with pa.OSFile(str(path), "wb") as sink:
            with ipc.new_file(sink, table.schema) as writer:
                writer.write(table)
    except ImportError:
        log.warning("pyarrow not installed — falling back to JSONL")
        _export_jsonl(rows, path.with_suffix(".jsonl"))


def _export_xlsx(rows: List[DatasetRow], path: Path) -> None:
    try:
        import openpyxl
        wb = openpyxl.Workbook()
        ws = wb.active
        if not rows:
            wb.save(path)
            return
        seen: dict = {}
        for r in rows:
            for k in r.fields:
                seen[k] = None
        headers = list(seen)
        ws.append(headers)
        for row in rows:
            ws.append([str(row.fields.get(h, "")) for h in headers])
        wb.save(path)
    except ImportError:
        log.warning("openpyxl not installed — falling back to CSV")
        _export_csv(rows, path.with_suffix(".csv"))


def _export_xml(rows: List[DatasetRow], path: Path) -> None:
    import xml.etree.ElementTree as ET
    root = ET.Element("dataset")
    for row in rows:
        item = ET.SubElement(root, "row", id=str(row.id))
        for k, v in row.fields.items():
            ET.SubElement(item, k).text = str(v)
    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    tree.write(path, encoding="utf-8", xml_declaration=True)


# ─── Project listing ──────────────────────────────────────────────────────────

def list_projects(base: Path = DEFAULT_BASE) -> List[ProjectSummary]:
    """
    List all saved projects sorted by last modified time.
    Reads only job.json + state.json per project — never loads rows.
    """
    projects_dir = base / "projects"
    if not projects_dir.exists():
        return []

    summaries: List[ProjectSummary] = []
    def _safe_mtime(p: Path) -> float:
        try:
            return p.stat().st_mtime
        except OSError:
            return 0.0

    for job_dir in sorted(projects_dir.iterdir(), key=_safe_mtime, reverse=True):
        if not job_dir.is_dir():
            continue
        try:
            job_id = UUID(job_dir.name)
        except ValueError:
            continue
        try:
            config = _sync_load_config(job_id, base)
            state  = _sync_load_state(job_id, base)
            if not config or not state:
                continue
            stat = job_dir.stat()
            summaries.append(ProjectSummary(
                job_id=config.job_id,
                name=config.name,
                status=state.status,
                pipeline_mode=state.pipeline_mode or "vibe",
                total_rows=config.total_rows,
                verified_rows=state.verified_rows,
                error_rows=state.error_rows,
                output_format=config.output_format,
                created_at=stat.st_ctime,
                updated_at=stat.st_mtime,
            ))
        except Exception as e:
            log.warning(f"Skipping malformed project {job_dir.name}: {e}")

    return summaries


def delete_project(job_id: UUID, base: Path = DEFAULT_BASE) -> None:
    import shutil
    d = _job_dir(job_id, base)
    if d.exists():
        shutil.rmtree(d)
        log.info(f"Deleted project {job_id}")
