"""Tests for intent_extract.py with mocked OpenAI API."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from harness.intent_extract import (
    build_messages,
    build_prompt,
    load_evidence,
    parse_draft_task,
    sample_frames,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

VALID_TASK_YAML = """\
task_id: "download-report"
version: "1.0"
environment: "browser"

goal:
  description: "Download the quarterly report from {{url}}"
  variables:
    url:
      type: "url"
      default: "https://example.com/reports"

preconditions:
  - "Browser is open"

setup_script: null

verification:
  primary:
    method: "programmatic"
    check: "file_exists('report.pdf')"

cleanup_script: null
"""

WRAPPED_YAML = f"""\
```yaml
{VALID_TASK_YAML}
```
"""


def _create_evidence_dir(tmp_path: Path, num_frames: int = 3, has_aria: bool = False) -> Path:
    """Create a minimal evidence directory for testing."""
    evidence_dir = tmp_path / "evidence"
    screenshots_dir = evidence_dir / "screenshots"
    screenshots_dir.mkdir(parents=True)

    for i in range(1, num_frames + 1):
        (screenshots_dir / f"{i:04d}_{1000 + i * 2}.png").write_bytes(b"\x89PNG fake")

    if has_aria:
        aria_dir = evidence_dir / "aria"
        aria_dir.mkdir()
        (aria_dir / "0001.yaml").write_text('AXApplication "Chrome"')
        if num_frames > 1:
            (aria_dir / f"{num_frames:04d}.yaml").write_text('AXApplication "Chrome"\n  AXWindow')

    manifest = {
        "task_name": "test-task",
        "captured_at": "2026-04-04T00:00:00+00:00",
        "capture_interval_ms": 2000,
        "total_frames": num_frames,
        "duration_seconds": (num_frames - 1) * 2,
        "platform": "darwin",
        "has_aria": has_aria,
        "has_transcript": False,
        "has_notes": False,
        "frames": [{"sequence": i, "timestamp": 1000 + i * 2} for i in range(1, num_frames + 1)],
    }
    (evidence_dir / "manifest.json").write_text(json.dumps(manifest))
    return evidence_dir


# ---------------------------------------------------------------------------
# load_evidence
# ---------------------------------------------------------------------------


class TestLoadEvidence:
    def test_loads_manifest(self, tmp_path: Path) -> None:
        evidence_dir = _create_evidence_dir(tmp_path)
        manifest = load_evidence(evidence_dir)
        assert manifest["task_name"] == "test-task"
        assert manifest["total_frames"] == 3

    def test_missing_manifest_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError, match="No manifest.json"):
            load_evidence(tmp_path)


# ---------------------------------------------------------------------------
# sample_frames
# ---------------------------------------------------------------------------


class TestSampleFrames:
    def test_returns_all_when_under_limit(self) -> None:
        paths = [Path(f"{i}.png") for i in range(5)]
        assert sample_frames(paths, max_frames=10) == paths

    def test_samples_to_max(self) -> None:
        paths = [Path(f"{i}.png") for i in range(20)]
        sampled = sample_frames(paths, max_frames=5)
        assert len(sampled) <= 5
        assert sampled[0] == paths[0]
        assert sampled[-1] == paths[-1]

    def test_includes_first_and_last(self) -> None:
        paths = [Path(f"{i}.png") for i in range(100)]
        sampled = sample_frames(paths, max_frames=3)
        assert sampled[0] == paths[0]
        assert sampled[-1] == paths[99]

    def test_single_frame(self) -> None:
        paths = [Path("0.png")]
        assert sample_frames(paths, max_frames=10) == paths


# ---------------------------------------------------------------------------
# build_prompt
# ---------------------------------------------------------------------------


class TestBuildPrompt:
    def test_basic_prompt(self) -> None:
        manifest = {"capture_interval_ms": 2000}
        prompt = build_prompt(manifest)
        assert "2.0 seconds apart" in prompt
        assert "task_id" in prompt

    def test_prompt_with_aria(self) -> None:
        manifest = {"capture_interval_ms": 1000}
        prompt = build_prompt(manifest, aria_first="AXApp", aria_last="AXWindow")
        assert "accessibility tree" in prompt
        assert "AXApp" in prompt
        assert "AXWindow" in prompt

    def test_prompt_with_transcript(self) -> None:
        manifest = {"capture_interval_ms": 2000}
        prompt = build_prompt(manifest, transcript="I'm downloading a file")
        assert "voice narration" in prompt
        assert "downloading a file" in prompt

    def test_prompt_without_extras(self) -> None:
        manifest = {"capture_interval_ms": 3000}
        prompt = build_prompt(manifest)
        assert "accessibility tree" not in prompt
        assert "voice narration" not in prompt
        assert "input events" not in prompt

    def test_prompt_with_events_context(self) -> None:
        manifest = {"capture_interval_ms": 2000}
        events_ctx = (
            "Additionally, here is a timeline of the user's input events "
            "during the recording:\n\n"
            "At t=1.0s clicked (500, 300)\n"
            "At t=2.0s typed 'hello'\n\n"
        )
        prompt = build_prompt(manifest, events_context=events_ctx)
        assert "input events" in prompt
        assert "clicked (500, 300)" in prompt
        assert "typed 'hello'" in prompt


# ---------------------------------------------------------------------------
# build_messages
# ---------------------------------------------------------------------------


class TestBuildMessages:
    def test_single_screenshot(self, tmp_path: Path) -> None:
        img = tmp_path / "test.png"
        img.write_bytes(b"\x89PNG test")

        messages = build_messages("test prompt", [img])
        assert len(messages) == 1
        content = messages[0]["content"]
        assert isinstance(content, list)
        assert len(content) == 2  # text + 1 image
        assert content[0]["type"] == "text"
        assert content[1]["type"] == "image_url"

    def test_multiple_screenshots(self, tmp_path: Path) -> None:
        imgs = []
        for i in range(3):
            img = tmp_path / f"{i}.png"
            img.write_bytes(b"\x89PNG test")
            imgs.append(img)

        messages = build_messages("prompt", imgs)
        content = messages[0]["content"]
        assert isinstance(content, list)
        assert len(content) == 4  # text + 3 images

    def test_base64_encoding(self, tmp_path: Path) -> None:
        img = tmp_path / "test.png"
        img.write_bytes(b"hello")

        messages = build_messages("prompt", [img])
        content = messages[0]["content"]
        assert isinstance(content, list)
        image_url = content[1]["image_url"]
        assert isinstance(image_url, dict)
        assert image_url["url"].startswith("data:image/png;base64,")


# ---------------------------------------------------------------------------
# parse_draft_task
# ---------------------------------------------------------------------------


class TestParseDraftTask:
    def test_parses_valid_yaml(self) -> None:
        task = parse_draft_task(VALID_TASK_YAML)
        assert task.task_id == "download-report"
        assert task.environment == "browser"
        assert "url" in task.goal.variables
        assert task.verification.primary.check == "file_exists('report.pdf')"

    def test_strips_markdown_wrapping(self) -> None:
        task = parse_draft_task(WRAPPED_YAML)
        assert task.task_id == "download-report"

    def test_invalid_yaml_raises(self) -> None:
        with pytest.raises(Exception):
            parse_draft_task("not: valid: yaml: {{broken}}")

    def test_missing_required_field_raises(self) -> None:
        with pytest.raises(Exception):
            parse_draft_task("task_id: test\nversion: '1.0'\n")


# ---------------------------------------------------------------------------
# author_task (integration with mocked OpenAI)
# ---------------------------------------------------------------------------


class TestAuthorTask:
    def test_author_writes_yaml(self, tmp_path: Path) -> None:
        from harness.intent_extract import author_task

        evidence_dir = _create_evidence_dir(tmp_path)
        output_path = tmp_path / "output" / "task.yaml"

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = VALID_TASK_YAML

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response

        with patch("harness.intent_extract.OpenAI", return_value=mock_client):
            result = author_task(evidence_dir, output_path)

        assert output_path.exists()
        assert "download-report" in result

    def test_author_dry_run_no_write(self, tmp_path: Path) -> None:
        from harness.intent_extract import author_task

        evidence_dir = _create_evidence_dir(tmp_path)
        output_path = tmp_path / "output" / "task.yaml"

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = VALID_TASK_YAML

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response

        with patch("harness.intent_extract.OpenAI", return_value=mock_client):
            result = author_task(evidence_dir, output_path, dry_run=True)

        assert not output_path.exists()
        assert "download-report" in result

    def test_author_saves_raw_on_validation_failure(self, tmp_path: Path) -> None:
        from harness.intent_extract import author_task

        evidence_dir = _create_evidence_dir(tmp_path)
        output_path = tmp_path / "output" / "task.yaml"

        invalid_yaml = "some_field: value\nanother: thing\n"
        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = invalid_yaml

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response

        with patch("harness.intent_extract.OpenAI", return_value=mock_client):
            author_task(evidence_dir, output_path)

        raw_path = output_path.with_suffix(".yaml.raw")
        assert raw_path.exists()
        assert raw_path.read_text() == invalid_yaml

    def test_author_with_aria_evidence(self, tmp_path: Path) -> None:
        from harness.intent_extract import author_task

        evidence_dir = _create_evidence_dir(tmp_path, has_aria=True)
        output_path = tmp_path / "output" / "task.yaml"

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = VALID_TASK_YAML

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response

        with patch("harness.intent_extract.OpenAI", return_value=mock_client):
            author_task(evidence_dir, output_path)

        # Verify the API was called with messages containing ARIA context
        call_args = mock_client.chat.completions.create.call_args
        messages = call_args.kwargs.get("messages") or call_args[1].get("messages")
        prompt_text = messages[0]["content"][0]["text"]
        assert "accessibility tree" in prompt_text
