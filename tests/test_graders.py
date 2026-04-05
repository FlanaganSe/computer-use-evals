"""Tests for programmatic and LLM-based graders."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from harness.graders import FORM_SUBMISSION_PATH, grade
from harness.types import Task, TaskGoal, TaskVerification, VerificationCheck


def _make_task(check_expr: str) -> Task:
    return Task(
        task_id="test-task",
        version="1.0",
        goal=TaskGoal(description="Test task"),
        verification=TaskVerification(
            primary=VerificationCheck(method="programmatic", check=check_expr),
        ),
    )


class TestFileExistsGrader:
    def test_passes_when_file_exists(self, tmp_path: Path) -> None:
        artifacts = tmp_path / "artifacts"
        artifacts.mkdir()
        (artifacts / "test.pdf").write_bytes(b"%PDF-1.4 test content")

        task = _make_task("file_exists('test.pdf')")
        result = grade(task, tmp_path)

        assert result.passed is True
        assert result.method == "file_exists"
        assert "size_bytes" in result.details

    def test_fails_when_file_missing(self, tmp_path: Path) -> None:
        artifacts = tmp_path / "artifacts"
        artifacts.mkdir()

        task = _make_task("file_exists('missing.pdf')")
        result = grade(task, tmp_path)

        assert result.passed is False
        assert result.method == "file_exists"
        assert "not found" in result.explanation

    def test_fails_when_artifacts_dir_missing(self, tmp_path: Path) -> None:
        task = _make_task("file_exists('test.pdf')")
        result = grade(task, tmp_path)

        assert result.passed is False

    def test_handles_double_quotes(self, tmp_path: Path) -> None:
        artifacts = tmp_path / "artifacts"
        artifacts.mkdir()
        (artifacts / "report.pdf").write_bytes(b"data")

        task = _make_task('file_exists("report.pdf")')
        result = grade(task, tmp_path)
        assert result.passed is True


class TestFormSubmittedGrader:
    def test_passes_with_matching_fields(self, tmp_path: Path) -> None:
        FORM_SUBMISSION_PATH.write_text(
            json.dumps({"name": "Jane Doe", "email": "jane@example.com"})
        )
        try:
            task = _make_task("form_submitted('Jane Doe', 'jane@example.com')")
            result = grade(task, tmp_path)
            assert result.passed is True
            assert result.method == "form_submitted"
        finally:
            FORM_SUBMISSION_PATH.unlink(missing_ok=True)

    def test_fails_with_wrong_name(self, tmp_path: Path) -> None:
        FORM_SUBMISSION_PATH.write_text(
            json.dumps({"name": "John Doe", "email": "jane@example.com"})
        )
        try:
            task = _make_task("form_submitted('Jane Doe', 'jane@example.com')")
            result = grade(task, tmp_path)
            assert result.passed is False
            assert "mismatch" in result.explanation
        finally:
            FORM_SUBMISSION_PATH.unlink(missing_ok=True)

    def test_fails_when_no_submission_file(self, tmp_path: Path) -> None:
        FORM_SUBMISSION_PATH.unlink(missing_ok=True)
        task = _make_task("form_submitted('Jane Doe', 'jane@example.com')")
        result = grade(task, tmp_path)
        assert result.passed is False
        assert "not found" in result.explanation


class TestGraderEdgeCases:
    def test_unknown_check_expression(self, tmp_path: Path) -> None:
        task = _make_task("unknown_check()")
        result = grade(task, tmp_path)
        assert result.passed is False
        assert "Unknown" in result.explanation

    def test_ax_contains_method_not_implemented(self, tmp_path: Path) -> None:
        task = Task(
            task_id="test",
            version="1.0",
            goal=TaskGoal(description="Test"),
            verification=TaskVerification(
                primary=VerificationCheck(method="ax_contains", role="AXTextArea", value="hello"),
            ),
        )
        result = grade(task, tmp_path)
        assert result.passed is False
        assert "not implemented" in result.explanation


# ---------------------------------------------------------------------------
# LLM judge grader
# ---------------------------------------------------------------------------


class TestLlmJudge:
    def test_llm_judge_passes(self, tmp_path: Path) -> None:
        task = Task(
            task_id="test",
            version="1.0",
            goal=TaskGoal(description="Save a file"),
            verification=TaskVerification(
                primary=VerificationCheck(
                    method="llm_judge",
                    prompt="Did the file get saved?",
                    threshold=0.5,
                ),
            ),
        )

        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(
                message=MagicMock(
                    content=json.dumps(
                        {"passed": True, "confidence": 0.9, "explanation": "File was saved"}
                    )
                )
            )
        ]

        with (
            patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}),
            patch("openai.OpenAI") as mock_openai_cls,
        ):
            mock_client = MagicMock()
            mock_openai_cls.return_value = mock_client
            mock_client.chat.completions.create.return_value = mock_response

            result = grade(task, tmp_path)

        assert result.passed is True
        assert result.method == "llm_judge"
        assert "saved" in result.explanation

    def test_llm_judge_fails_below_threshold(self, tmp_path: Path) -> None:
        task = Task(
            task_id="test",
            version="1.0",
            goal=TaskGoal(description="Save a file"),
            verification=TaskVerification(
                primary=VerificationCheck(
                    method="llm_judge",
                    prompt="Did the file get saved?",
                    threshold=0.9,
                ),
            ),
        )

        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(
                message=MagicMock(
                    content=json.dumps(
                        {"passed": True, "confidence": 0.6, "explanation": "Maybe saved"}
                    )
                )
            )
        ]

        with (
            patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}),
            patch("openai.OpenAI") as mock_openai_cls,
        ):
            mock_client = MagicMock()
            mock_openai_cls.return_value = mock_client
            mock_client.chat.completions.create.return_value = mock_response

            result = grade(task, tmp_path)

        assert result.passed is False
        assert "threshold" in result.explanation

    def test_llm_judge_no_api_key(self, tmp_path: Path) -> None:
        task = Task(
            task_id="test",
            version="1.0",
            goal=TaskGoal(description="Test"),
            verification=TaskVerification(
                primary=VerificationCheck(method="llm_judge", prompt="Did it work?"),
            ),
        )

        with patch.dict("os.environ", {}, clear=True):
            result = grade(task, tmp_path)

        assert result.passed is False
        assert "OPENAI_API_KEY" in result.explanation

    def test_llm_judge_with_default_prompt(self, tmp_path: Path) -> None:
        task = Task(
            task_id="test",
            version="1.0",
            goal=TaskGoal(description="Open TextEdit"),
            verification=TaskVerification(
                primary=VerificationCheck(method="llm_judge"),
            ),
        )

        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(
                message=MagicMock(
                    content=json.dumps(
                        {"passed": True, "confidence": 0.8, "explanation": "App opened"}
                    )
                )
            )
        ]

        with (
            patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}),
            patch("openai.OpenAI") as mock_openai_cls,
        ):
            mock_client = MagicMock()
            mock_openai_cls.return_value = mock_client
            mock_client.chat.completions.create.return_value = mock_response

            result = grade(task, tmp_path)

        assert result.passed is True
