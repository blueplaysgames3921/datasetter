"""
agents/analyser.py

The Analyser activates when files are attached to the job.
It can handle: CSV, JSONL, JSON, Parquet, PDF, images, video, plain text.

For datasets: summarises schema, themes, formatting, quality, field distributions.
For images/video: describes content, extracts constraints and relevant information.
For PDFs/text: extracts key information, domain knowledge, constraints.

Output is a structured context string fed to the Interpreter.
The Analyser never generates data — it only extracts and summarises.

Model choice: local preferred (cost-intensive task).
  Default: Gemma 4 E4B Q4 / LFM 6B+ Q4 via Ollama
  Vision tasks (images/video): requires a vision-capable model.
"""

from __future__ import annotations

import csv
import json
import logging
import mimetypes
from pathlib import Path
from typing import Any, Dict, List, Tuple

from core.litellm_router import LiteLLMRouter
from core.models import AgentID, AgentStatus, JobConfig
from utils.events import Emitter

log = logging.getLogger(__name__)


SYSTEM_PROMPT = """You are the Analyser (Indexer) agent in Datasetter, an AI dataset generation pipeline.

You receive content extracted from files attached by the user and produce a structured analysis
that the Interpreter will use to understand the job context.

Your analysis must cover:
1. SCHEMA — what fields/columns exist, their types, formats
2. THEMES — what the content is about, core topics, domain
3. QUALITY — sample quality, consistency, any issues observed
4. CONSTRAINTS — implicit rules you observe (length norms, tone, format conventions)
5. DISTRIBUTION — category/label distribution if applicable
6. SAMPLE — 3-5 representative examples (verbatim from the content)
7. GAPS — what's missing or underrepresented
8. RECOMMENDATIONS — what the Generator should know to match or extend this data

Be specific and concrete. The Interpreter needs actionable information, not vague summaries.
Output a comprehensive plain-text analysis. No JSON required here — prose is fine.
"""


# ─── File type handlers ───────────────────────────────────────────────────────


def _read_jsonl(path: Path, max_rows: int = 100) -> Tuple[List[dict], int]:
    rows, total = [], 0
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            total += 1
            if len(rows) < max_rows:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return rows, total


def _read_csv(path: Path, max_rows: int = 100) -> Tuple[List[dict], int]:
    rows, total = [], 0
    with path.open("r", encoding="utf-8", errors="replace", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            total += 1
            if len(rows) < max_rows:
                rows.append(dict(row))
    return rows, total


def _read_json(path: Path, max_rows: int = 100) -> Tuple[List[dict], int]:
    data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    if isinstance(data, list):
        return data[:max_rows], len(data)
    return [data], 1


def _read_parquet(path: Path, max_rows: int = 100) -> Tuple[List[dict], int]:
    try:
        import pandas as pd
        df = pd.read_parquet(path)
        total = len(df)
        sample = df.head(max_rows).to_dict(orient="records")
        return sample, total
    except ImportError:
        raise RuntimeError("pandas/pyarrow not installed — cannot read Parquet files.")


def _read_text(path: Path, max_chars: int = 8000) -> str:
    content = path.read_text(encoding="utf-8", errors="replace")
    if len(content) > max_chars:
        return content[:max_chars] + f"\n\n[... truncated, {len(content)} total chars]"
    return content


def _read_pdf(path: Path, max_chars: int = 8000) -> str:
    """Extract text from PDF using pypdf."""
    try:
        from pypdf import PdfReader
        reader = PdfReader(str(path))
        pages  = []
        chars  = 0
        for page in reader.pages:
            text = page.extract_text() or ""
            pages.append(text)
            chars += len(text)
            if chars >= max_chars:
                break
        content = "\n\n".join(pages)
        if len(content) > max_chars:
            return content[:max_chars] + "\n\n[... truncated]"
        return content
    except ImportError:
        raise RuntimeError("pypdf not installed — cannot read PDF files.")


def _file_to_content(path: Path) -> Dict[str, Any]:
    """
    Read a file and return a dict with:
      type: "tabular" | "text" | "image" | "video" | "unknown"
      content: str or list[dict]
      total_rows: int (for tabular)
      note: str
    """
    suffix = path.suffix.lower()
    mime   = mimetypes.guess_type(str(path))[0] or ""

    # Tabular
    if suffix == ".jsonl":
        rows, total = _read_jsonl(path)
        return {"type": "tabular", "content": rows, "total_rows": total}
    elif suffix in (".csv", ".tsv"):
        rows, total = _read_csv(path)
        return {"type": "tabular", "content": rows, "total_rows": total}
    elif suffix == ".json":
        rows, total = _read_json(path)
        return {"type": "tabular", "content": rows, "total_rows": total}
    elif suffix == ".parquet":
        rows, total = _read_parquet(path)
        return {"type": "tabular", "content": rows, "total_rows": total}

    # Text / PDF
    elif suffix == ".pdf":
        text = _read_pdf(path)
        return {"type": "text", "content": text}
    elif suffix in (".txt", ".md", ".rst", ".html", ".xml"):
        text = _read_text(path)
        return {"type": "text", "content": text}

    # Images — return path for vision model
    elif suffix in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp") or mime.startswith("image/"):
        return {"type": "image", "path": str(path)}

    # Video — return path for vision model (if supported)
    elif suffix in (".mp4", ".mov", ".avi", ".mkv", ".webm") or mime.startswith("video/"):
        return {"type": "video", "path": str(path)}

    else:
        # Try reading as text
        try:
            text = _read_text(path)
            return {"type": "text", "content": text}
        except Exception:
            return {"type": "unknown", "content": "", "note": f"Could not read {suffix} file."}


def _tabular_summary(rows: List[dict], total_rows: int) -> str:
    """Build a text summary of tabular data for the LLM."""
    if not rows:
        return "No rows could be read."

    fields = list(rows[0].keys())
    lines  = [
        f"Total rows: {total_rows} (showing first {len(rows)})",
        f"Fields ({len(fields)}): {', '.join(fields)}",
        "",
        "Sample rows:",
    ]
    for i, row in enumerate(rows[:5], 1):
        lines.append(f"\n[Row {i}]")
        for k, v in row.items():
            v_str = str(v)
            if len(v_str) > 200:
                v_str = v_str[:200] + "..."
            lines.append(f"  {k}: {v_str}")

    # Field length stats
    lines.append("\nField length statistics (chars):")
    for field in fields:
        lengths = [len(str(r.get(field, ""))) for r in rows if r.get(field)]
        if lengths:
            lines.append(
                f"  {field}: min={min(lengths)}, "
                f"max={max(lengths)}, "
                f"avg={sum(lengths)//len(lengths)}"
            )

    return "\n".join(lines)


# ─── Analyser agent ───────────────────────────────────────────────────────────


class Analyser:

    def __init__(self, router: LiteLLMRouter, emitter: Emitter):
        self.router  = router
        self.emitter = emitter

    async def run(self, config: JobConfig) -> str:
        """
        Analyse all attached files and return a consolidated context string.
        """
        if not config.attached_files:
            return ""

        self.emitter.agent_status(AgentID.ANALYSER, AgentStatus.RUNNING, current_task="Analysing files")
        self.emitter.log(AgentID.ANALYSER, f"Analysing {len(config.attached_files)} attached file(s).")

        analyses: List[str] = []

        for file_path_str in config.attached_files:
            path = Path(file_path_str).expanduser()

            if not path.exists():
                self.emitter.log(AgentID.ANALYSER, f"File not found, skipping: {path.name}")
                continue

            self.emitter.log(AgentID.ANALYSER, f"Reading: {path.name}")

            try:
                file_data = _file_to_content(path)
                analysis  = await self._analyse_file(path.name, file_data, config.prompt)
                analyses.append(f"### {path.name}\n{analysis}")
            except Exception as e:
                self.emitter.log(AgentID.ANALYSER, f"Error reading {path.name}: {e}")
                log.exception(f"Analyser error on {path}")

        if not analyses:
            self.emitter.agent_status(AgentID.ANALYSER, AgentStatus.DONE, current_task="No usable files")
            return ""

        consolidated = "\n\n".join(analyses)

        # If multiple files, run a consolidation pass
        if len(analyses) > 1:
            consolidated = await self._consolidate(consolidated, config.prompt)

        self.emitter.log(AgentID.ANALYSER, f"Analysis complete. {len(analyses)} file(s) processed.")
        self.emitter.agent_status(AgentID.ANALYSER, AgentStatus.DONE, current_task="Analysis complete")

        return consolidated

    async def _analyse_file(self, name: str, file_data: Dict[str, Any], user_prompt: str) -> str:
        ftype = file_data["type"]

        if ftype == "tabular":
            raw_summary = _tabular_summary(file_data["content"], file_data.get("total_rows", 0))
            user_msg = (
                f"File: {name}\n\n"
                f"User's dataset request: {user_prompt}\n\n"
                f"File content summary:\n{raw_summary}\n\n"
                "Produce a thorough analysis of this dataset covering all required sections."
            )

        elif ftype == "text":
            content = file_data["content"]
            user_msg = (
                f"File: {name}\n\n"
                f"User's dataset request: {user_prompt}\n\n"
                f"File content:\n{content}\n\n"
                "Analyse this document. Extract schema, themes, constraints, and recommendations "
                "for dataset generation based on this content."
            )

        elif ftype == "image":
            # Vision model required
            # Note: for actual vision, we'd send the image bytes.
            # This is a text-only fallback description request.
            # Full vision support: encode image as base64 and include in message content.
            # Handled below with _analyse_image_with_vision().
            return await self._analyse_image_with_vision(name, file_data["path"], user_prompt)

        elif ftype == "video":
            return (
                f"Video file detected ({name}). "
                "Frame extraction and vision analysis not yet implemented. "
                "Please describe the video content manually in the extra context field."
            )

        else:
            return f"Could not analyse {name}: {file_data.get('note', 'unknown format')}"

        try:
            text, model = await self.router.complete(
                agent_id=AgentID.ANALYSER,
                messages=[{"role": "user", "content": user_msg}],
                system=SYSTEM_PROMPT,
                temperature=0.2,
                max_tokens=3000,
            )
            return text
        except Exception as e:
            log.error(f"Analyser LLM call failed for {name}: {e}")
            return f"Analysis failed for {name}: {e}"

    async def _analyse_image_with_vision(self, name: str, path: str, user_prompt: str) -> str:
        """
        Analyse an image using a vision-capable model routed through LiteLLMRouter.
        Falls back to a text description request if vision is unavailable.
        """
        import base64

        try:
            with open(path, "rb") as f:
                image_b64 = base64.b64encode(f.read()).decode("utf-8")

            suffix   = Path(path).suffix.lower().lstrip(".")
            mime_map = {
                "jpg": "image/jpeg", "jpeg": "image/jpeg",
                "png": "image/png",  "webp": "image/webp", "gif": "image/gif",
            }
            mime_type = mime_map.get(suffix, "image/png")

            messages = [{
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime_type};base64,{image_b64}"}
                    },
                    {
                        "type": "text",
                        "text": (
                            f"File: {name}\n\n"
                            f"User's dataset request: {user_prompt}\n\n"
                            "Describe this image in full detail. Extract all visible text, labels, "
                            "structure, domain knowledge, and any information that could inform "
                            "dataset generation based on the user's request."
                        )
                    }
                ]
            }]

            # Route through the router so API keys, retries, and fallback all apply.
            # Vision requires a cloud model — override to use Analyser's cloud assignment
            # if it has one, otherwise fall back to Interpreter's cloud model.
            text, _ = await self.router.complete(
                agent_id=AgentID.ANALYSER,
                messages=messages,
                system=SYSTEM_PROMPT,
                temperature=0.2,
                max_tokens=2000,
            )
            return text

        except Exception as e:
            log.error(f"Vision analysis failed for {name}: {e}")
            return (
                f"Image analysis failed for '{name}': {e}. "
                "Please describe the image content manually in the extra context field."
            )

    async def _consolidate(self, combined_analyses: str, user_prompt: str) -> str:
        """
        When multiple files are attached, consolidate their analyses into one coherent context.
        """
        self.emitter.log(AgentID.ANALYSER, "Consolidating multi-file analysis.")
        text, _ = await self.router.complete(
            agent_id=AgentID.ANALYSER,
            messages=[{
                "role": "user",
                "content": (
                    f"User's dataset request: {user_prompt}\n\n"
                    f"Below are individual analyses of {len(combined_analyses.split('###'))-1} files.\n\n"
                    f"{combined_analyses}\n\n"
                    "Consolidate these into a single unified analysis. Identify what's consistent "
                    "across files, what varies, and what the Interpreter needs to know to build "
                    "a coherent generation plan from all these sources."
                )
            }],
            system=SYSTEM_PROMPT,
            temperature=0.2,
            max_tokens=3000,
        )
        return text
