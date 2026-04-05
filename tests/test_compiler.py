"""Tests for the draft → compiled task compiler."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from harness.compiler import (
    CompileError,
    CompileMetadata,
    DraftTask,
    compile_draft,
    compile_draft_file,
    parse_draft_yaml,
)
from harness.types import Task

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _minimal_draft(**overrides: object) -> dict:
    """Return a minimal valid draft dict, with optional overrides."""
    base: dict = {
        "task_id": "test-task",
        "version": "1.0",
        "goal": {
            "description": "Do the thing",
            "variables": {},
        },
        "verification": {
            "primary": {
                "method": "programmatic",
                "check": "file_exists('out.txt')",
            },
        },
    }
    base.update(overrides)
    return base


def _draft_with_vars(**overrides: object) -> dict:
    """Draft that uses variables in description and checks."""
    base: dict = {
        "task_id": "var-task",
        "version": "1.0",
        "goal": {
            "description": "Save {{content}} to {{filename}}",
            "variables": {
                "content": {"type": "string", "default": "hello"},
                "filename": {"type": "string", "default": "out.txt"},
            },
        },
        "verification": {
            "primary": {
                "method": "programmatic",
                "check": "file_contains('{{filename}}', '{{content}}')",
            },
        },
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# parse_draft_yaml
# ---------------------------------------------------------------------------


class TestParseDraftYaml:
    def test_parses_valid_yaml(self) -> None:
        raw = yaml.dump(_minimal_draft())
        draft = parse_draft_yaml(raw)
        assert draft.task_id == "test-task"

    def test_strips_code_fences(self) -> None:
        raw = "```yaml\n" + yaml.dump(_minimal_draft()) + "\n```"
        draft = parse_draft_yaml(raw)
        assert draft.task_id == "test-task"

    def test_rejects_non_mapping(self) -> None:
        with pytest.raises(ValueError, match="YAML mapping"):
            parse_draft_yaml("- item1\n- item2\n")

    def test_rejects_missing_required(self) -> None:
        with pytest.raises(Exception):
            parse_draft_yaml("task_id: x\nversion: '1.0'\n")

    def test_parses_compile_metadata(self) -> None:
        data = _minimal_draft(
            compile_metadata={
                "source_evidence": "/evidence/test",
                "authoring_model": "gpt-5.4",
                "authored_at": "2026-04-05T12:00:00Z",
            }
        )
        draft = parse_draft_yaml(yaml.dump(data))
        assert draft.compile_metadata is not None
        assert draft.compile_metadata.authoring_model == "gpt-5.4"

    def test_parses_agent_brief(self) -> None:
        data = _minimal_draft()
        data["goal"]["agent_brief"] = "Step 1: do this. Step 2: do that."
        draft = parse_draft_yaml(yaml.dump(data))
        assert draft.goal.agent_brief == "Step 1: do this. Step 2: do that."


# ---------------------------------------------------------------------------
# compile_draft — success cases
# ---------------------------------------------------------------------------


class TestCompileDraftSuccess:
    def test_compiles_minimal_draft(self) -> None:
        draft = DraftTask.model_validate(_minimal_draft())
        task = compile_draft(draft, validate_scripts=False)
        assert isinstance(task, Task)
        assert task.task_id == "test-task"

    def test_agent_brief_derived_from_description(self) -> None:
        draft = DraftTask.model_validate(_minimal_draft())
        task = compile_draft(draft, validate_scripts=False)
        assert task.goal.agent_brief == "Do the thing"

    def test_explicit_agent_brief_preserved(self) -> None:
        data = _minimal_draft()
        data["goal"]["agent_brief"] = "Agent: just do it"
        draft = DraftTask.model_validate(data)
        task = compile_draft(draft, validate_scripts=False)
        assert task.goal.agent_brief == "Agent: just do it"

    def test_compiles_draft_with_variables(self) -> None:
        draft = DraftTask.model_validate(_draft_with_vars())
        task = compile_draft(draft, validate_scripts=False)
        assert "content" in task.goal.variables
        assert "filename" in task.goal.variables

    def test_compiles_draft_with_milestones(self) -> None:
        data = _minimal_draft(
            milestones=[
                {
                    "id": "step1",
                    "description": "First step done",
                    "check": {"method": "programmatic", "check": "file_exists('step1.txt')"},
                },
            ]
        )
        draft = DraftTask.model_validate(data)
        task = compile_draft(draft, validate_scripts=False)
        assert len(task.milestones) == 1
        assert task.milestones[0].id == "step1"

    def test_compile_metadata_stripped(self) -> None:
        data = _minimal_draft(
            compile_metadata={
                "source_evidence": "/evidence/test",
                "authoring_model": "gpt-5.4",
            }
        )
        draft = DraftTask.model_validate(data)
        task = compile_draft(draft, validate_scripts=False)
        # Task model doesn't have compile_metadata
        assert not hasattr(task, "compile_metadata") or "compile_metadata" not in task.model_fields

    def test_preserves_environment(self) -> None:
        data = _minimal_draft(environment="macos_desktop")
        draft = DraftTask.model_validate(data)
        task = compile_draft(draft, validate_scripts=False)
        assert task.environment == "macos_desktop"

    def test_preserves_preconditions(self) -> None:
        data = _minimal_draft(preconditions=["macOS available", "AX permission granted"])
        draft = DraftTask.model_validate(data)
        task = compile_draft(draft, validate_scripts=False)
        assert len(task.preconditions) == 2

    def test_compiles_llm_judge_with_prompt(self) -> None:
        data = _minimal_draft(
            verification={
                "primary": {"method": "llm_judge", "prompt": "Did it work?"},
            }
        )
        draft = DraftTask.model_validate(data)
        task = compile_draft(draft, validate_scripts=False)
        assert task.verification.primary.method == "llm_judge"


# ---------------------------------------------------------------------------
# compile_draft — failure cases
# ---------------------------------------------------------------------------


class TestCompileDraftFailures:
    def test_rejects_boolean_check_expression(self) -> None:
        data = _minimal_draft(
            verification={
                "primary": {
                    "method": "programmatic",
                    "check": "file_exists('a') and file_exists('b')",
                },
            }
        )
        draft = DraftTask.model_validate(data)
        with pytest.raises(CompileError) as exc_info:
            compile_draft(draft, validate_scripts=False)
        assert "boolean expression" in str(exc_info.value)

    def test_rejects_unknown_check_function(self) -> None:
        data = _minimal_draft(
            verification={
                "primary": {
                    "method": "programmatic",
                    "check": "totally_made_up('x')",
                },
            }
        )
        draft = DraftTask.model_validate(data)
        with pytest.raises(CompileError) as exc_info:
            compile_draft(draft, validate_scripts=False)
        assert "totally_made_up" in str(exc_info.value)

    def test_rejects_unresolved_variable_ref(self) -> None:
        data = _minimal_draft()
        data["goal"]["description"] = "Save to {{output_dir}}"
        # No variables declared
        draft = DraftTask.model_validate(data)
        with pytest.raises(CompileError) as exc_info:
            compile_draft(draft, validate_scripts=False)
        assert "output_dir" in str(exc_info.value)
        assert "Unresolved variable" in str(exc_info.value)

    def test_rejects_unresolved_variable_in_check(self) -> None:
        data = _minimal_draft(
            verification={
                "primary": {
                    "method": "programmatic",
                    "check": "file_exists('{{missing_var}}')",
                },
            }
        )
        draft = DraftTask.model_validate(data)
        with pytest.raises(CompileError) as exc_info:
            compile_draft(draft, validate_scripts=False)
        assert "missing_var" in str(exc_info.value)

    def test_rejects_unresolved_variable_in_milestone(self) -> None:
        data = _minimal_draft(
            milestones=[
                {
                    "id": "m1",
                    "description": "Check {{nonexistent}}",
                    "check": {"method": "programmatic", "check": "file_exists('ok.txt')"},
                },
            ]
        )
        draft = DraftTask.model_validate(data)
        with pytest.raises(CompileError) as exc_info:
            compile_draft(draft, validate_scripts=False)
        assert "nonexistent" in str(exc_info.value)

    def test_rejects_missing_setup_script(self, tmp_path: Path) -> None:
        data = _minimal_draft(setup_script="tasks/test/setup.py")
        draft = DraftTask.model_validate(data)
        with pytest.raises(CompileError) as exc_info:
            compile_draft(draft, task_dir=tmp_path, validate_scripts=True)
        assert "setup_script" in str(exc_info.value)

    def test_rejects_missing_script_check_path(self, tmp_path: Path) -> None:
        data = _minimal_draft(
            verification={
                "primary": {
                    "method": "programmatic",
                    "check": "script_check('tasks/test/verify.py')",
                },
            }
        )
        draft = DraftTask.model_validate(data)
        with pytest.raises(CompileError) as exc_info:
            compile_draft(draft, task_dir=tmp_path, validate_scripts=True)
        assert "script_check" in str(exc_info.value)

    def test_collects_multiple_errors(self) -> None:
        """Compile reports ALL errors, not just the first."""
        data = _minimal_draft(
            verification={
                "primary": {
                    "method": "programmatic",
                    "check": "file_exists('a') and file_exists('b')",
                },
            },
        )
        data["goal"]["description"] = "Do {{unknown_thing}}"
        draft = DraftTask.model_validate(data)
        with pytest.raises(CompileError) as exc_info:
            compile_draft(draft, validate_scripts=False)
        assert len(exc_info.value.errors) >= 2

    def test_rejects_llm_judge_without_prompt(self) -> None:
        data = _minimal_draft(
            verification={
                "primary": {"method": "llm_judge"},
            }
        )
        draft = DraftTask.model_validate(data)
        with pytest.raises(CompileError) as exc_info:
            compile_draft(draft, validate_scripts=False)
        assert "no prompt" in str(exc_info.value)

    def test_rejects_boolean_in_milestone_check(self) -> None:
        data = _minimal_draft(
            milestones=[
                {
                    "id": "m1",
                    "description": "Bad milestone",
                    "check": {
                        "method": "programmatic",
                        "check": "file_exists('a') or file_exists('b')",
                    },
                },
            ]
        )
        draft = DraftTask.model_validate(data)
        with pytest.raises(CompileError) as exc_info:
            compile_draft(draft, validate_scripts=False)
        assert "boolean expression" in str(exc_info.value)


# ---------------------------------------------------------------------------
# compile_draft — script validation with existing scripts
# ---------------------------------------------------------------------------


class TestCompileDraftScriptValidation:
    def test_passes_when_scripts_exist(self, tmp_path: Path) -> None:
        # Create the script files
        script_dir = tmp_path / "tasks" / "test"
        script_dir.mkdir(parents=True)
        (script_dir / "setup.py").write_text("# setup")
        (script_dir / "verify.py").write_text("# verify")

        data = _minimal_draft(
            setup_script="tasks/test/setup.py",
            verification={
                "primary": {
                    "method": "programmatic",
                    "check": "script_check('tasks/test/verify.py')",
                },
            },
        )
        draft = DraftTask.model_validate(data)
        task = compile_draft(draft, task_dir=tmp_path, validate_scripts=True)
        assert task.task_id == "test-task"

    def test_skips_script_validation_when_no_task_dir(self) -> None:
        data = _minimal_draft(setup_script="tasks/test/setup.py")
        draft = DraftTask.model_validate(data)
        # Should not raise even though script doesn't exist
        task = compile_draft(draft, task_dir=None, validate_scripts=True)
        assert task.task_id == "test-task"


# ---------------------------------------------------------------------------
# compile_draft_file — round-trip file compilation
# ---------------------------------------------------------------------------


class TestCompileDraftFile:
    def test_compiles_file_to_task_yaml(self, tmp_path: Path) -> None:
        draft_data = _minimal_draft()
        draft_path = tmp_path / "draft.yaml"
        draft_path.write_text(yaml.dump(draft_data))

        compile_draft_file(draft_path, validate_scripts=False)
        output_path = tmp_path / "task.yaml"
        assert output_path.exists()

        # Verify the output is valid Task YAML
        compiled = yaml.safe_load(output_path.read_text())
        assert compiled["task_id"] == "test-task"
        assert compiled["goal"]["agent_brief"] == "Do the thing"

    def test_custom_output_path(self, tmp_path: Path) -> None:
        draft_data = _minimal_draft()
        draft_path = tmp_path / "draft.yaml"
        draft_path.write_text(yaml.dump(draft_data))

        output_path = tmp_path / "output" / "compiled.yaml"
        compile_draft_file(draft_path, output_path=output_path, validate_scripts=False)
        assert output_path.exists()

    def test_compile_file_rejects_invalid(self, tmp_path: Path) -> None:
        data = _minimal_draft()
        data["goal"]["description"] = "Use {{bad_var}}"
        draft_path = tmp_path / "draft.yaml"
        draft_path.write_text(yaml.dump(data))

        with pytest.raises(CompileError):
            compile_draft_file(draft_path, validate_scripts=False)

    def test_compiled_task_loadable_by_task_loader(self, tmp_path: Path) -> None:
        """Compiled output should be loadable by load_task(strict=True)."""
        from harness.task_loader import load_task

        draft_data = _draft_with_vars()
        draft_path = tmp_path / "draft.yaml"
        draft_path.write_text(yaml.dump(draft_data))

        compile_draft_file(draft_path, validate_scripts=False)
        output_path = tmp_path / "task.yaml"

        task = load_task(output_path, strict=True)
        assert task.task_id == "var-task"
        # Variables should be substituted
        assert "{{content}}" not in task.goal.description
        assert "hello" in task.goal.description

    def test_compile_metadata_not_in_output(self, tmp_path: Path) -> None:
        data = _minimal_draft(
            compile_metadata={
                "source_evidence": "/evidence/test",
                "authoring_model": "gpt-5.4",
            }
        )
        draft_path = tmp_path / "draft.yaml"
        draft_path.write_text(yaml.dump(data))

        compile_draft_file(draft_path, validate_scripts=False)
        output = yaml.safe_load((tmp_path / "task.yaml").read_text())
        assert "compile_metadata" not in output


# ---------------------------------------------------------------------------
# DraftTask and CompileMetadata models
# ---------------------------------------------------------------------------


class TestDraftTaskModel:
    def test_draft_task_from_dict(self) -> None:
        draft = DraftTask.model_validate(_minimal_draft())
        assert draft.task_id == "test-task"
        assert draft.compile_metadata is None

    def test_draft_task_with_metadata(self) -> None:
        data = _minimal_draft(
            compile_metadata={
                "source_evidence": "/tmp/evidence",
                "authoring_model": "gpt-5.4",
                "authored_at": "2026-04-05",
            }
        )
        draft = DraftTask.model_validate(data)
        assert draft.compile_metadata is not None
        assert draft.compile_metadata.source_evidence == "/tmp/evidence"


class TestCompileMetadataModel:
    def test_all_fields_optional(self) -> None:
        meta = CompileMetadata()
        assert meta.source_evidence is None
        assert meta.authoring_model is None
        assert meta.authored_at is None

    def test_partial_fields(self) -> None:
        meta = CompileMetadata(authoring_model="gpt-5.4")
        assert meta.authoring_model == "gpt-5.4"
        assert meta.source_evidence is None
