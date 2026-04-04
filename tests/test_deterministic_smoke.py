"""End-to-end smoke test: run the deterministic baseline and verify artifacts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness.runner import run_task


@pytest.mark.slow
def test_deterministic_browser_download(tmp_path: Path) -> None:
    """Full end-to-end: run deterministic adapter on browser-download task."""
    run_dir = run_task(
        task_path="tasks/browser_download/task.yaml",
        adapter_name="deterministic",
        max_steps=30,
        runs_dir=str(tmp_path / "runs"),
    )

    # Run directory exists
    assert run_dir.exists()

    # All expected files exist
    assert (run_dir / "task.yaml").exists()
    assert (run_dir / "config.json").exists()
    assert (run_dir / "trace.json").exists()
    assert (run_dir / "grade.json").exists()
    assert (run_dir / "report.md").exists()
    assert (run_dir / "artifacts").is_dir()
    assert (run_dir / "screenshots").is_dir()

    # Downloaded file exists
    assert (run_dir / "artifacts" / "test.pdf").exists()
    assert (run_dir / "artifacts" / "test.pdf").stat().st_size > 0

    # Trace structure
    trace = json.loads((run_dir / "trace.json").read_text())
    assert trace["task_id"] == "browser-download"
    assert trace["adapter"] == "deterministic"
    assert trace["outcome"] == "pass"
    assert trace["failure_category"] is None
    assert trace["total_steps"] >= 2
    assert len(trace["steps"]) >= 2

    # Steps have expected structure
    for step in trace["steps"]:
        assert "step" in step
        assert "action" in step
        assert "result" in step
        assert "type" in step["action"]

    # Grade structure
    grade = json.loads((run_dir / "grade.json").read_text())
    assert grade["passed"] is True
    assert grade["method"] == "file_exists"

    # Report is non-empty markdown
    report = (run_dir / "report.md").read_text()
    assert "# Run Report" in report
    assert "PASS" in report

    # Config
    config = json.loads((run_dir / "config.json").read_text())
    assert config["adapter"] == "deterministic"


@pytest.mark.slow
def test_deterministic_browser_form_fill(tmp_path: Path) -> None:
    """Full end-to-end: run deterministic adapter on browser-form-fill task."""
    run_dir = run_task(
        task_path="tasks/browser_form_fill/task.yaml",
        adapter_name="deterministic",
        max_steps=30,
        runs_dir=str(tmp_path / "runs"),
    )

    assert run_dir.exists()

    # All expected files exist
    assert (run_dir / "trace.json").exists()
    assert (run_dir / "grade.json").exists()
    assert (run_dir / "report.md").exists()

    # Trace structure
    trace = json.loads((run_dir / "trace.json").read_text())
    assert trace["task_id"] == "browser-form-fill"
    assert trace["adapter"] == "deterministic"
    assert trace["outcome"] == "pass"
    assert trace["total_steps"] >= 4

    # Grade
    grade = json.loads((run_dir / "grade.json").read_text())
    assert grade["passed"] is True
    assert grade["method"] == "form_submitted"

    # Report
    report = (run_dir / "report.md").read_text()
    assert "PASS" in report


@pytest.mark.slow
def test_deterministic_failing_run_is_inspectable(tmp_path: Path) -> None:
    """A task with no matching deterministic script should fail clearly."""
    # Create a task YAML that the deterministic adapter doesn't know
    task_yaml = tmp_path / "unknown_task.yaml"
    task_yaml.write_text(
        """
task_id: "unknown-task"
version: "1.0"
goal:
  description: "An unknown task"
verification:
  primary:
    method: "programmatic"
    check: "file_exists('nope.txt')"
"""
    )

    run_dir = run_task(
        task_path=str(task_yaml),
        adapter_name="deterministic",
        max_steps=10,
        runs_dir=str(tmp_path / "runs"),
    )

    trace = json.loads((run_dir / "trace.json").read_text())
    assert trace["outcome"] == "fail"

    # The trace should show what went wrong
    assert len(trace["steps"]) >= 1
    last_step = trace["steps"][-1]
    assert "fail" in last_step["result"]

    # Grade should also fail
    grade = json.loads((run_dir / "grade.json").read_text())
    assert grade["passed"] is False

    # Report should show FAIL
    report = (run_dir / "report.md").read_text()
    assert "FAIL" in report
