"""Tests for task YAML loading, validation, and variable substitution."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from harness.task_loader import load_task, validate_check_expression
from harness.types import VerificationCheck


@pytest.fixture()
def task_dir(tmp_path: Path) -> Path:
    return tmp_path


def _write_task(path: Path, data: dict) -> Path:  # type: ignore[type-arg]
    task_file = path / "task.yaml"
    task_file.write_text(yaml.dump(data))
    return task_file


VALID_TASK = {
    "task_id": "browser-download",
    "version": "1.0",
    "goal": {
        "description": "Download the file from {{url}}",
        "variables": {
            "url": {"type": "url", "default": "http://localhost:8765/test.pdf"},
            "filename": {"type": "string", "default": "test.pdf"},
        },
    },
    "preconditions": ["Browser is available"],
    "setup_script": "tasks/browser_download/setup.py",
    "verification": {
        "primary": {
            "method": "programmatic",
            "check": "file_exists('{{filename}}')",
        },
    },
}


class TestLoadTask:
    def test_loads_valid_task(self, task_dir: Path) -> None:
        path = _write_task(task_dir, VALID_TASK)
        task = load_task(path)
        assert task.task_id == "browser-download"
        assert task.version == "1.0"
        assert len(task.goal.variables) == 2

    def test_substitutes_defaults(self, task_dir: Path) -> None:
        path = _write_task(task_dir, VALID_TASK)
        task = load_task(path)
        assert "http://localhost:8765/test.pdf" in task.goal.description
        assert "{{url}}" not in task.goal.description

    def test_substitutes_overrides(self, task_dir: Path) -> None:
        path = _write_task(task_dir, VALID_TASK)
        task = load_task(path, overrides={"url": "http://example.com/other.pdf"})
        assert "http://example.com/other.pdf" in task.goal.description

    def test_substitutes_in_verification(self, task_dir: Path) -> None:
        path = _write_task(task_dir, VALID_TASK)
        task = load_task(path, overrides={"filename": "report.pdf"})
        assert task.verification.primary.check is not None
        assert "report.pdf" in task.verification.primary.check

    def test_preserves_unresolved_variables(self, task_dir: Path) -> None:
        data = {**VALID_TASK, "goal": {**VALID_TASK["goal"], "description": "Get {{unknown}}"}}
        path = _write_task(task_dir, data)
        task = load_task(path)
        assert "{{unknown}}" in task.goal.description


class TestValidationErrors:
    def test_missing_task_id(self, task_dir: Path) -> None:
        data = {k: v for k, v in VALID_TASK.items() if k != "task_id"}
        path = _write_task(task_dir, data)
        with pytest.raises(Exception):
            load_task(path)

    def test_missing_verification(self, task_dir: Path) -> None:
        data = {k: v for k, v in VALID_TASK.items() if k != "verification"}
        path = _write_task(task_dir, data)
        with pytest.raises(Exception):
            load_task(path)

    def test_non_mapping_yaml(self, task_dir: Path) -> None:
        path = task_dir / "task.yaml"
        path.write_text("- item1\n- item2\n")
        with pytest.raises(ValueError, match="YAML mapping"):
            load_task(path)

    def test_invalid_verification_method(self, task_dir: Path) -> None:
        data = {
            **VALID_TASK,
            "verification": {"primary": {"method": "invalid", "check": "x"}},
        }
        path = _write_task(task_dir, data)
        with pytest.raises(Exception):
            load_task(path)

    def test_missing_goal(self, task_dir: Path) -> None:
        data = {k: v for k, v in VALID_TASK.items() if k != "goal"}
        path = _write_task(task_dir, data)
        with pytest.raises(Exception):
            load_task(path)


# ---------------------------------------------------------------------------
# Check expression validation
# ---------------------------------------------------------------------------


class TestCheckValidation:
    def test_valid_file_exists(self) -> None:
        check = VerificationCheck(method="programmatic", check="file_exists('test.pdf')")
        warnings = validate_check_expression(check)
        assert warnings == []

    def test_valid_file_contains(self) -> None:
        check = VerificationCheck(method="programmatic", check="file_contains('/tmp/x', 'hello')")
        warnings = validate_check_expression(check)
        assert warnings == []

    def test_rejects_boolean_and(self) -> None:
        check = VerificationCheck(
            method="programmatic",
            check="app_opened('TextEdit') and not form_submitted('Sean', 'x')",
        )
        with pytest.raises(ValueError, match="boolean expression"):
            validate_check_expression(check)

    def test_rejects_boolean_or(self) -> None:
        check = VerificationCheck(
            method="programmatic",
            check="file_exists('a') or file_exists('b')",
        )
        with pytest.raises(ValueError, match="boolean expression"):
            validate_check_expression(check)

    def test_warns_unknown_function(self) -> None:
        check = VerificationCheck(method="programmatic", check="unknown_func('x')")
        warnings = validate_check_expression(check)
        assert len(warnings) == 1
        assert "unknown_func" in warnings[0]

    def test_warns_llm_judge_no_prompt(self) -> None:
        check = VerificationCheck(method="llm_judge")
        warnings = validate_check_expression(check)
        assert len(warnings) == 1
        assert "no prompt" in warnings[0]

    def test_valid_llm_judge_with_prompt(self) -> None:
        check = VerificationCheck(method="llm_judge", prompt="Did it work?")
        warnings = validate_check_expression(check)
        assert warnings == []


class TestStrictValidation:
    def test_strict_rejects_boolean_task(self, task_dir: Path) -> None:
        data = {
            **VALID_TASK,
            "verification": {
                "primary": {
                    "method": "programmatic",
                    "check": "file_exists('a') and file_exists('b')",
                }
            },
        }
        path = _write_task(task_dir, data)
        with pytest.raises(ValueError, match="boolean expression"):
            load_task(path, strict=True)

    def test_non_strict_warns_only(self, task_dir: Path) -> None:
        data = {
            **VALID_TASK,
            "verification": {
                "primary": {
                    "method": "programmatic",
                    "check": "unknown_func('x')",
                }
            },
        }
        path = _write_task(task_dir, data)
        with pytest.raises(ValueError, match="unknown_func"):
            load_task(path, strict=True)

    def test_non_strict_loads_with_warnings(self, task_dir: Path) -> None:
        data = {
            **VALID_TASK,
            "verification": {
                "primary": {
                    "method": "programmatic",
                    "check": "unknown_func('x')",
                }
            },
        }
        path = _write_task(task_dir, data)
        task = load_task(path, strict=False)
        assert task.task_id == "browser-download"


class TestMilestoneLoading:
    def test_loads_task_with_milestones(self, task_dir: Path) -> None:
        data = {
            **VALID_TASK,
            "milestones": [
                {
                    "id": "file_created",
                    "description": "File exists",
                    "check": {"method": "programmatic", "check": "file_exists('test.pdf')"},
                }
            ],
        }
        path = _write_task(task_dir, data)
        task = load_task(path)
        assert len(task.milestones) == 1
        assert task.milestones[0].id == "file_created"

    def test_loads_task_without_milestones(self, task_dir: Path) -> None:
        path = _write_task(task_dir, VALID_TASK)
        task = load_task(path)
        assert task.milestones == []

    def test_milestone_check_validated(self, task_dir: Path) -> None:
        data = {
            **VALID_TASK,
            "milestones": [
                {
                    "id": "bad",
                    "description": "Bad check",
                    "check": {
                        "method": "programmatic",
                        "check": "foo('x') and bar('y')",
                    },
                }
            ],
        }
        path = _write_task(task_dir, data)
        with pytest.raises(ValueError, match="boolean expression"):
            load_task(path)
