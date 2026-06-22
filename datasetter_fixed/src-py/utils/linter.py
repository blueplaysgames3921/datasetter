"""
utils/linter.py

Lightweight rule-based linter that runs BEFORE the Verifier.
Scripter calls this after generating each row or batch.
Catches obvious mechanical violations cheaply — no LLM call needed.

The Verifier handles semantic/logic/consistency checks.
The Linter handles: format, length, required fields, forbidden patterns.
"""

from __future__ import annotations

import re
from typing import List, Tuple

from core.models import DatasetRow, ErrorType, JobConfig, RowStatus, VerifierError


class LintResult:
    def __init__(self, row_id: int):
        self.row_id  = row_id
        self.errors: List[VerifierError] = []

    @property
    def passed(self) -> bool:
        return len(self.errors) == 0

    @property
    def fatal(self) -> bool:
        return any(e.severity == "fatal" for e in self.errors)


class Linter:
    """
    Runs cheap, deterministic checks on generated rows.
    No LLM calls — pure rule evaluation.
    """

    def __init__(self, config: JobConfig):
        self.config      = config
        self.constraints = {fc.field: fc for fc in config.field_constraints}

    def lint_row(self, row: DatasetRow) -> LintResult:
        result = LintResult(row.id)

        for field, constraint in self.constraints.items():
            value = row.fields.get(field)

            # Required field missing
            if constraint.required and (value is None or str(value).strip() == ""):
                result.errors.append(VerifierError(
                    row_id=row.id,
                    error_type=ErrorType.FORMAT,
                    field=field,
                    description=f"Required field '{field}' is missing or empty.",
                    fix_instruction=f"Generate a valid value for field '{field}'. {constraint.description}",
                    severity="fatal",
                ))
                continue

            if value is None:
                continue

            str_value = str(value)

            # Min length
            if constraint.min_length is not None and len(str_value) < constraint.min_length:
                result.errors.append(VerifierError(
                    row_id=row.id,
                    error_type=ErrorType.LENGTH,
                    field=field,
                    description=(
                        f"Field '{field}' is too short: {len(str_value)} chars "
                        f"(minimum {constraint.min_length})."
                    ),
                    fix_instruction=(
                        f"Expand the '{field}' field to at least {constraint.min_length} characters. "
                        f"Add more detail, examples, or elaboration. Current: '{str_value[:100]}...'"
                    ),
                    severity="minor",
                ))

            # Max length
            if constraint.max_length is not None and len(str_value) > constraint.max_length:
                result.errors.append(VerifierError(
                    row_id=row.id,
                    error_type=ErrorType.LENGTH,
                    field=field,
                    description=(
                        f"Field '{field}' is too long: {len(str_value)} chars "
                        f"(maximum {constraint.max_length})."
                    ),
                    fix_instruction=(
                        f"Shorten the '{field}' field to at most {constraint.max_length} characters "
                        f"without losing key information."
                    ),
                    severity="minor",
                ))

            # Forbidden patterns
            for pattern in constraint.forbidden_patterns:
                try:
                    if re.search(pattern, str_value, re.IGNORECASE):
                        result.errors.append(VerifierError(
                            row_id=row.id,
                            error_type=ErrorType.CONSTRAINT,
                            field=field,
                            description=f"Field '{field}' contains forbidden pattern: '{pattern}'.",
                            fix_instruction=f"Rewrite field '{field}' to not contain '{pattern}'.",
                            severity="minor",
                        ))
                except re.error:
                    pass  # Bad regex in config — skip

        # Global: check for placeholder-style content
        for field, value in row.fields.items():
            str_value = str(value)
            placeholders = [
                r"\[[A-Z][A-Z_\s]{2,}\]",  # [INSERT HERE], [YOUR TEXT] — all-caps only
                r"<[A-Z][A-Z_\s]{2,}>",       # <PLACEHOLDER>, <YOUR NAME> — all-caps only
                r"\bTODO\b",
                r"\bPLACEHOLDER\b",
                r"\bINSERT HERE\b",           # not "insert" alone — too many false positives
                r"\bFILL IN\b",
            ]
            for ph in placeholders:
                if re.search(ph, str_value, re.IGNORECASE):
                    result.errors.append(VerifierError(
                        row_id=row.id,
                        error_type=ErrorType.FORMAT,
                        field=field,
                        description=f"Field '{field}' contains placeholder text: '{str_value[:60]}'.",
                        fix_instruction=f"Replace the placeholder in '{field}' with actual content.",
                        severity="fatal",
                    ))
                    break

        return result

    def lint_batch(self, rows: List[DatasetRow]) -> List[LintResult]:
        return [self.lint_row(row) for row in rows]

    def apply_results(self, rows: List[DatasetRow], results: List[LintResult]) -> Tuple[List[DatasetRow], List[DatasetRow]]:
        """
        Apply lint results to rows.
        Returns (clean_rows, flagged_rows).
        Modifies rows in place.
        """
        clean   = []
        flagged = []

        for row, result in zip(rows, results):
            if result.passed:
                # Don't override OK — let Verifier do that
                clean.append(row)
            else:
                row.errors = result.errors
                row.status = RowStatus.ERROR
                flagged.append(row)

        return clean, flagged

    def summary(self, results: List[LintResult]) -> dict:
        total   = len(results)
        passed  = sum(1 for r in results if r.passed)
        failed  = total - passed
        fatal   = sum(1 for r in results if r.fatal)
        errors  = [e for r in results for e in r.errors]

        by_type: dict = {}
        for e in errors:
            by_type[e.error_type.value] = by_type.get(e.error_type.value, 0) + 1

        return {
            "total": total,
            "passed": passed,
            "failed": failed,
            "fatal": fatal,
            "error_types": by_type,
        }
