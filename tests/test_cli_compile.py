"""Tests for the CLI compile and author→compile workflow."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from harness.cli import main


def _minimal_draft_data(**overrides: object) -> dict:
    base: dict = {
        "task_id": "cli-test",
        "version": "1.0",
        "goal": {
            "description": "Do something",
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


class TestCompileCommand:
    def test_compile_produces_task_yaml(self, tmp_path: Path) -> None:
        draft_path = tmp_path / "draft.yaml"
        draft_path.write_text(yaml.dump(_minimal_draft_data()))

        main(["compile", str(draft_path), "--no-validate-scripts"])

        output = tmp_path / "task.yaml"
        assert output.exists()
        compiled = yaml.safe_load(output.read_text())
        assert compiled["task_id"] == "cli-test"
        assert compiled["goal"]["agent_brief"] == "Do something"

    def test_compile_custom_output(self, tmp_path: Path) -> None:
        draft_path = tmp_path / "draft.yaml"
        draft_path.write_text(yaml.dump(_minimal_draft_data()))
        output = tmp_path / "custom" / "compiled.yaml"

        main(["compile", str(draft_path), "--output", str(output), "--no-validate-scripts"])

        assert output.exists()

    def test_compile_rejects_invalid_draft(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        data = _minimal_draft_data()
        data["goal"]["description"] = "Use {{bad_var}}"
        draft_path = tmp_path / "draft.yaml"
        draft_path.write_text(yaml.dump(data))

        with pytest.raises(SystemExit) as exc_info:
            main(["compile", str(draft_path), "--no-validate-scripts"])
        assert exc_info.value.code == 1

        captured = capsys.readouterr()
        assert "bad_var" in captured.err
        assert "Compile failed" in captured.err

    def test_compile_validates_scripts(self, tmp_path: Path) -> None:
        data = _minimal_draft_data(setup_script="tasks/test/setup.py")
        draft_path = tmp_path / "draft.yaml"
        draft_path.write_text(yaml.dump(data))

        with pytest.raises(SystemExit) as exc_info:
            main(["compile", str(draft_path), "--task-dir", str(tmp_path)])
        assert exc_info.value.code == 1

    def test_compile_skips_script_validation(self, tmp_path: Path) -> None:
        data = _minimal_draft_data(setup_script="tasks/test/setup.py")
        draft_path = tmp_path / "draft.yaml"
        draft_path.write_text(yaml.dump(data))

        # --no-validate-scripts should skip the script check
        main(["compile", str(draft_path), "--no-validate-scripts"])

        output = tmp_path / "task.yaml"
        assert output.exists()

    def test_compile_strips_compile_metadata(self, tmp_path: Path) -> None:
        data = _minimal_draft_data(
            compile_metadata={
                "source_evidence": "/evidence/test",
                "authoring_model": "gpt-5.4",
            }
        )
        draft_path = tmp_path / "draft.yaml"
        draft_path.write_text(yaml.dump(data))

        main(["compile", str(draft_path), "--no-validate-scripts"])

        output = tmp_path / "task.yaml"
        compiled = yaml.safe_load(output.read_text())
        assert "compile_metadata" not in compiled

    def test_compile_with_variables(self, tmp_path: Path) -> None:
        data = _minimal_draft_data()
        data["goal"] = {
            "description": "Save {{content}} to {{filename}}",
            "variables": {
                "content": {"type": "string", "default": "hello"},
                "filename": {"type": "string", "default": "out.txt"},
            },
        }
        data["verification"]["primary"]["check"] = "file_contains('{{filename}}', '{{content}}')"
        draft_path = tmp_path / "draft.yaml"
        draft_path.write_text(yaml.dump(data))

        main(["compile", str(draft_path), "--no-validate-scripts"])

        output = tmp_path / "task.yaml"
        assert output.exists()

    def test_compile_with_milestones(self, tmp_path: Path) -> None:
        data = _minimal_draft_data(
            milestones=[
                {
                    "id": "step1",
                    "description": "Step 1 done",
                    "check": {"method": "programmatic", "check": "file_exists('step1.txt')"},
                }
            ]
        )
        draft_path = tmp_path / "draft.yaml"
        draft_path.write_text(yaml.dump(data))

        main(["compile", str(draft_path), "--no-validate-scripts"])

        output = tmp_path / "task.yaml"
        compiled = yaml.safe_load(output.read_text())
        assert len(compiled["milestones"]) == 1

    def test_compiled_output_loadable_by_runner(self, tmp_path: Path) -> None:
        """Compiled task.yaml should be loadable by load_task(strict=True)."""
        from harness.task_loader import load_task

        data = _minimal_draft_data()
        draft_path = tmp_path / "draft.yaml"
        draft_path.write_text(yaml.dump(data))

        main(["compile", str(draft_path), "--no-validate-scripts"])

        task = load_task(tmp_path / "task.yaml", strict=True)
        assert task.task_id == "cli-test"


class TestCompileErrorMessages:
    def test_multiple_errors_all_reported(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        data = _minimal_draft_data(
            verification={
                "primary": {
                    "method": "programmatic",
                    "check": "bad_func('a') and bad_func('b')",
                },
            },
        )
        data["goal"]["description"] = "Do {{unknown}}"
        draft_path = tmp_path / "draft.yaml"
        draft_path.write_text(yaml.dump(data))

        with pytest.raises(SystemExit):
            main(["compile", str(draft_path), "--no-validate-scripts"])

        captured = capsys.readouterr()
        assert "error(s)" in captured.err
