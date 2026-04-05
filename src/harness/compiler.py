"""Compile draft task artifacts into validated runtime Task objects.

The compiler is the trust boundary between human/VLM-authored drafts and
runnable eval tasks.  It validates check expressions, variable references,
and script paths — catching problems that would otherwise surface only at
runtime.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field

from harness.task_loader import validate_check_expression
from harness.types import (
    Milestone,
    Task,
    TaskGoal,
    TaskVerification,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Draft models — authored artifact shape
# ---------------------------------------------------------------------------


class CompileMetadata(BaseModel):
    """Provenance information attached to a draft by the author step."""

    source_evidence: str | None = None
    authoring_model: str | None = None
    authored_at: str | None = None


class DraftTask(BaseModel):
    """A draft task artifact produced by ``harness author``.

    Structurally close to :class:`Task` but carries optional
    compile-time-only fields (``compile_metadata``, ``goal.agent_brief``)
    that the compiler uses during validation and normalization.
    """

    task_id: str
    version: str
    goal: TaskGoal
    preconditions: list[str] = Field(default_factory=list)
    setup_script: str | None = None
    verification: TaskVerification
    cleanup_script: str | None = None
    environment: str | None = None
    milestones: list[Milestone] = Field(default_factory=list)
    compile_metadata: CompileMetadata | None = None


# ---------------------------------------------------------------------------
# Compile errors
# ---------------------------------------------------------------------------


class CompileError(Exception):
    """Raised when a draft fails compile-time validation."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__(f"Compile failed with {len(errors)} error(s):\n" + "\n".join(errors))


# ---------------------------------------------------------------------------
# Compile-time validation helpers
# ---------------------------------------------------------------------------

_VAR_PATTERN = re.compile(r"\{\{(\w+)\}\}")


def _find_variable_refs(text: str) -> set[str]:
    """Return all ``{{var}}`` references in *text*."""
    return set(_VAR_PATTERN.findall(text))


def _collect_text_fields(draft: DraftTask) -> list[str]:
    """Gather all text fields that may contain ``{{var}}`` placeholders."""
    texts: list[str] = [draft.goal.description]
    if draft.goal.agent_brief:
        texts.append(draft.goal.agent_brief)
    for pre in draft.preconditions:
        texts.append(pre)
    if draft.verification.primary.check:
        texts.append(draft.verification.primary.check)
    if draft.verification.fallback and draft.verification.fallback.check:
        texts.append(draft.verification.fallback.check)
    for ms in draft.milestones:
        texts.append(ms.description)
        if ms.check.check:
            texts.append(ms.check.check)
    return texts


def _validate_variable_refs(draft: DraftTask) -> list[str]:
    """Check that every ``{{var}}`` reference maps to a declared variable."""
    declared = set(draft.goal.variables.keys())
    errors: list[str] = []
    for text in _collect_text_fields(draft):
        for ref in _find_variable_refs(text):
            if ref not in declared:
                errors.append(
                    f"Unresolved variable reference '{{{{{ref}}}}}' — "
                    f"not declared in goal.variables (declared: {sorted(declared) or 'none'})"
                )
    # Deduplicate while preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for e in errors:
        if e not in seen:
            seen.add(e)
            unique.append(e)
    return unique


def _validate_scripts(draft: DraftTask, task_dir: Path | None) -> list[str]:
    """Check that referenced script paths exist on disk."""
    if task_dir is None:
        return []
    errors: list[str] = []
    for label, script in [
        ("setup_script", draft.setup_script),
        ("cleanup_script", draft.cleanup_script),
    ]:
        if script and not (task_dir / script).exists():
            errors.append(f"{label} path does not exist: {script} (resolved from {task_dir})")

    # Check script_check() references in verification checks
    all_checks = [draft.verification.primary]
    if draft.verification.fallback:
        all_checks.append(draft.verification.fallback)
    for ms in draft.milestones:
        all_checks.append(ms.check)
    for check in all_checks:
        if check.method == "programmatic" and check.check:
            m = re.match(r"script_check\(['\"](.+?)['\"]\)", check.check)
            if m:
                script_path = m.group(1)
                if not (task_dir / script_path).exists():
                    errors.append(
                        f"script_check path does not exist: {script_path} "
                        f"(resolved from {task_dir})"
                    )
    return errors


def _validate_checks(draft: DraftTask) -> list[str]:
    """Validate all check expressions at compile time (strict mode)."""
    errors: list[str] = []
    all_checks = [draft.verification.primary]
    if draft.verification.fallback:
        all_checks.append(draft.verification.fallback)
    for ms in draft.milestones:
        all_checks.append(ms.check)

    for check in all_checks:
        try:
            warnings = validate_check_expression(check)
            errors.extend(warnings)
        except ValueError as exc:
            errors.append(str(exc))
    return errors


# ---------------------------------------------------------------------------
# Core compile function
# ---------------------------------------------------------------------------


def compile_draft(
    draft: DraftTask,
    *,
    task_dir: Path | None = None,
    validate_scripts: bool = True,
) -> Task:
    """Compile a :class:`DraftTask` into a validated runtime :class:`Task`.

    Raises :class:`CompileError` if validation fails.

    Args:
        draft: The draft task to compile.
        task_dir: Project root for resolving relative script paths.
            If ``None``, script-existence checks are skipped.
        validate_scripts: Whether to check that script paths exist on disk.
    """
    errors: list[str] = []

    # 1. Check expression validation (strict)
    errors.extend(_validate_checks(draft))

    # 2. Variable reference completeness
    errors.extend(_validate_variable_refs(draft))

    # 3. Script path existence
    if validate_scripts and task_dir is not None:
        errors.extend(_validate_scripts(draft, task_dir))

    if errors:
        raise CompileError(errors)

    # 4. Normalize: DraftTask → Task
    #    - Derive agent_brief from description if not explicitly set
    #    - Strip compile_metadata (not part of runtime contract)
    goal = TaskGoal(
        description=draft.goal.description,
        agent_brief=draft.goal.agent_brief or draft.goal.description,
        variables=draft.goal.variables,
    )

    return Task(
        task_id=draft.task_id,
        version=draft.version,
        goal=goal,
        preconditions=draft.preconditions,
        setup_script=draft.setup_script,
        verification=draft.verification,
        cleanup_script=draft.cleanup_script,
        environment=draft.environment,
        milestones=draft.milestones,
    )


# ---------------------------------------------------------------------------
# File-level compile
# ---------------------------------------------------------------------------


def parse_draft_yaml(raw_yaml: str) -> DraftTask:
    """Parse raw YAML text into a :class:`DraftTask`.

    Strips markdown code fences if present.
    """
    cleaned = raw_yaml.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"```(?:yaml)?\s*\n?", "", cleaned)
        cleaned = cleaned.rstrip("`").strip()
    data = yaml.safe_load(cleaned)
    if not isinstance(data, dict):
        msg = f"Draft file must contain a YAML mapping, got {type(data).__name__}"
        raise ValueError(msg)
    return DraftTask.model_validate(data)


def compile_draft_file(
    draft_path: Path,
    output_path: Path | None = None,
    *,
    task_dir: Path | None = None,
    validate_scripts: bool = True,
) -> Task:
    """Load a draft YAML, compile it, and optionally write the compiled task.

    Args:
        draft_path: Path to the draft YAML file.
        output_path: Where to write the compiled task YAML. If ``None``,
            defaults to ``task.yaml`` in the same directory as the draft.
        task_dir: Project root for resolving script paths.
        validate_scripts: Whether to validate script path existence.

    Returns:
        The compiled :class:`Task`.
    """
    raw = draft_path.read_text()
    draft = parse_draft_yaml(raw)
    task = compile_draft(draft, task_dir=task_dir, validate_scripts=validate_scripts)

    if output_path is None:
        output_path = draft_path.parent / "task.yaml"

    # Serialize the compiled task
    data: dict[str, Any] = task.model_dump(by_alias=True, exclude_none=True)
    compiled_yaml = yaml.dump(data, default_flow_style=False, sort_keys=False)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(compiled_yaml)
    logger.info("Compiled task written to %s", output_path)

    return task
