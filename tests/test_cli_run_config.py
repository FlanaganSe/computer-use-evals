"""Tests for CLI config-driven run execution."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from harness.cli import main
from harness.types import GraderResult, Trace


def _write_config(
    path: Path,
    adapter: str = "deterministic",
    tasks: list[str] | None = None,
    max_steps: int = 30,
    trial_count: int = 1,
) -> None:
    data = {
        "adapter": adapter,
        "tasks": tasks if tasks is not None else ["tasks/a/task.yaml"],
        "max_steps": max_steps,
        "trial_count": trial_count,
    }
    path.write_text(yaml.dump(data))


def _make_run_artifacts(runs_dir: Path, task_id: str, adapter: str, suffix: str = "") -> Path:
    """Create a fake run directory with trace and grade artifacts."""
    run_dir = runs_dir / f"run_{task_id}_{adapter}{suffix}"
    run_dir.mkdir(parents=True, exist_ok=True)
    trace = Trace(
        task_id=task_id,
        adapter=adapter,
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
        completed_at=datetime(2026, 1, 1, 0, 1, tzinfo=UTC),
        outcome="pass",
        total_steps=3,
    )
    grade = GraderResult(passed=True, method="test", explanation="OK")
    (run_dir / "trace.json").write_text(trace.model_dump_json())
    (run_dir / "grade.json").write_text(grade.model_dump_json())
    return run_dir


# ---------------------------------------------------------------------------
# Validation tests
# ---------------------------------------------------------------------------


class TestRunConfigValidation:
    """CLI validation for --config vs positional task and --adapter."""

    def test_config_and_task_exclusive(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        cfg = tmp_path / "cfg.yaml"
        _write_config(cfg)
        with pytest.raises(SystemExit) as exc_info:
            main(["run", "task.yaml", "--config", str(cfg)])
        assert exc_info.value.code == 1
        assert "cannot specify both" in capsys.readouterr().err.lower()

    def test_config_and_adapter_exclusive(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        cfg = tmp_path / "cfg.yaml"
        _write_config(cfg)
        with pytest.raises(SystemExit) as exc_info:
            main(["run", "--config", str(cfg), "--adapter", "deterministic"])
        assert exc_info.value.code == 1
        assert "adapter" in capsys.readouterr().err.lower()

    def test_task_requires_adapter(self, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit) as exc_info:
            main(["run", "task.yaml"])
        assert exc_info.value.code == 1
        assert "adapter" in capsys.readouterr().err.lower()

    def test_no_task_no_config(self, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit) as exc_info:
            main(["run"])
        assert exc_info.value.code == 1

    def test_config_file_not_found(self, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit) as exc_info:
            main(["run", "--config", "nonexistent.yaml"])
        assert exc_info.value.code == 1
        assert "not found" in capsys.readouterr().err.lower()

    def test_config_invalid_adapter(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        cfg = tmp_path / "cfg.yaml"
        _write_config(cfg, adapter="no_such_adapter")
        with pytest.raises(SystemExit) as exc_info:
            main(["run", "--config", str(cfg)])
        assert exc_info.value.code == 1
        assert "no_such_adapter" in capsys.readouterr().err

    def test_config_empty_tasks(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        cfg = tmp_path / "cfg.yaml"
        _write_config(cfg, tasks=[])
        with pytest.raises(SystemExit) as exc_info:
            main(["run", "--config", str(cfg)])
        assert exc_info.value.code == 1
        assert "no tasks" in capsys.readouterr().err.lower()

    def test_config_empty_file(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        cfg = tmp_path / "cfg.yaml"
        cfg.write_text("")
        with pytest.raises(SystemExit) as exc_info:
            main(["run", "--config", str(cfg)])
        assert exc_info.value.code == 1
        assert "not valid yaml" in capsys.readouterr().err.lower()

    def test_config_missing_adapter_field(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        cfg = tmp_path / "cfg.yaml"
        cfg.write_text(yaml.dump({"tasks": ["tasks/a/task.yaml"]}))
        with pytest.raises(SystemExit) as exc_info:
            main(["run", "--config", str(cfg)])
        assert exc_info.value.code == 1
        assert "invalid config" in capsys.readouterr().err.lower()

    def test_config_trial_count_zero(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        cfg = tmp_path / "cfg.yaml"
        _write_config(cfg, trial_count=0)
        with pytest.raises(SystemExit) as exc_info:
            main(["run", "--config", str(cfg)])
        assert exc_info.value.code == 1
        assert "trial_count" in capsys.readouterr().err.lower()


# ---------------------------------------------------------------------------
# Execution tests
# ---------------------------------------------------------------------------


class TestRunConfigExecution:
    """Config-driven run orchestration and summary output."""

    def test_executes_all_tasks(self, tmp_path: Path) -> None:
        runs_dir = tmp_path / "runs"
        cfg = tmp_path / "cfg.yaml"
        _write_config(cfg, tasks=["tasks/a/task.yaml", "tasks/b/task.yaml"])

        call_count = 0

        def mock_run(task_path: str, adapter_name: str, max_steps: int, runs_dir: str) -> Path:
            nonlocal call_count
            call_count += 1
            tid = Path(task_path).parent.name
            return _make_run_artifacts(Path(runs_dir), tid, adapter_name, f"_{call_count}")

        with patch("harness.cli.run_task", side_effect=mock_run):
            main(["run", "--config", str(cfg), "--runs-dir", str(runs_dir)])

        assert call_count == 2

    def test_trial_count_multiplies_runs(self, tmp_path: Path) -> None:
        runs_dir = tmp_path / "runs"
        cfg = tmp_path / "cfg.yaml"
        _write_config(cfg, tasks=["tasks/a/task.yaml"], trial_count=3)

        call_args: list[str] = []
        idx = 0

        def mock_run(task_path: str, adapter_name: str, max_steps: int, runs_dir: str) -> Path:
            nonlocal idx
            idx += 1
            call_args.append(task_path)
            return _make_run_artifacts(Path(runs_dir), f"a_{idx}", adapter_name)

        with patch("harness.cli.run_task", side_effect=mock_run):
            main(["run", "--config", str(cfg), "--runs-dir", str(runs_dir)])

        assert len(call_args) == 3
        assert all(a == "tasks/a/task.yaml" for a in call_args)

    def test_max_steps_from_config(self, tmp_path: Path) -> None:
        runs_dir = tmp_path / "runs"
        cfg = tmp_path / "cfg.yaml"
        _write_config(cfg, max_steps=15)

        captured_steps: list[int] = []

        def mock_run(task_path: str, adapter_name: str, max_steps: int, runs_dir: str) -> Path:
            captured_steps.append(max_steps)
            return _make_run_artifacts(Path(runs_dir), "a", adapter_name)

        with patch("harness.cli.run_task", side_effect=mock_run):
            main(["run", "--config", str(cfg), "--runs-dir", str(runs_dir)])

        assert captured_steps == [15]

    def test_prints_comparison_summary(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        runs_dir = tmp_path / "runs"
        cfg = tmp_path / "cfg.yaml"
        _write_config(cfg)

        def mock_run(task_path: str, adapter_name: str, max_steps: int, runs_dir: str) -> Path:
            return _make_run_artifacts(Path(runs_dir), "a", adapter_name)

        with patch("harness.cli.run_task", side_effect=mock_run):
            main(["run", "--config", str(cfg), "--runs-dir", str(runs_dir)])

        captured = capsys.readouterr()
        assert "Comparison Report" in captured.out

    def test_prints_progress(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        runs_dir = tmp_path / "runs"
        cfg = tmp_path / "cfg.yaml"
        _write_config(cfg, tasks=["tasks/a/task.yaml", "tasks/b/task.yaml"])

        idx = 0

        def mock_run(task_path: str, adapter_name: str, max_steps: int, runs_dir: str) -> Path:
            nonlocal idx
            idx += 1
            return _make_run_artifacts(Path(runs_dir), f"t_{idx}", adapter_name)

        with patch("harness.cli.run_task", side_effect=mock_run):
            main(["run", "--config", str(cfg), "--runs-dir", str(runs_dir)])

        captured = capsys.readouterr()
        assert "[1/2]" in captured.out
        assert "[2/2]" in captured.out

    def test_trial_label_in_progress(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        runs_dir = tmp_path / "runs"
        cfg = tmp_path / "cfg.yaml"
        _write_config(cfg, tasks=["tasks/a/task.yaml"], trial_count=2)

        idx = 0

        def mock_run(task_path: str, adapter_name: str, max_steps: int, runs_dir: str) -> Path:
            nonlocal idx
            idx += 1
            return _make_run_artifacts(Path(runs_dir), f"a_{idx}", adapter_name)

        with patch("harness.cli.run_task", side_effect=mock_run):
            main(["run", "--config", str(cfg), "--runs-dir", str(runs_dir)])

        captured = capsys.readouterr()
        assert "trial 1/2" in captured.out
        assert "trial 2/2" in captured.out

    def test_continues_after_run_error(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        runs_dir = tmp_path / "runs"
        cfg = tmp_path / "cfg.yaml"
        _write_config(cfg, tasks=["tasks/bad/task.yaml", "tasks/good/task.yaml"])

        call_count = 0

        def mock_run(task_path: str, adapter_name: str, max_steps: int, runs_dir: str) -> Path:
            nonlocal call_count
            call_count += 1
            if "bad" in task_path:
                msg = "task file not found"
                raise FileNotFoundError(msg)
            return _make_run_artifacts(Path(runs_dir), "good", adapter_name)

        with patch("harness.cli.run_task", side_effect=mock_run):
            main(["run", "--config", str(cfg), "--runs-dir", str(runs_dir)])

        assert call_count == 2
        captured = capsys.readouterr()
        assert "error:" in captured.out
        assert "Comparison Report" in captured.out

    def test_adapter_passed_from_config(self, tmp_path: Path) -> None:
        runs_dir = tmp_path / "runs"
        cfg = tmp_path / "cfg.yaml"
        _write_config(cfg, adapter="deterministic")

        captured_adapter: list[str] = []

        def mock_run(task_path: str, adapter_name: str, max_steps: int, runs_dir: str) -> Path:
            captured_adapter.append(adapter_name)
            return _make_run_artifacts(Path(runs_dir), "a", adapter_name)

        with patch("harness.cli.run_task", side_effect=mock_run):
            main(["run", "--config", str(cfg), "--runs-dir", str(runs_dir)])

        assert captured_adapter == ["deterministic"]
