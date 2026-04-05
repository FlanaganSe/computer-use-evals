"""Tests for intent_extract.py with mocked OpenAI API."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from harness.intent_extract import (
    _truncate_aria,
    build_aligned_events_context,
    build_messages,
    build_prompt,
    load_evidence,
    load_sampled_aria,
    parse_draft_task,
    sample_frames,
    select_aligned_screenshots,
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

        # Verify the API was called with messages containing sampled ARIA context
        call_args = mock_client.chat.completions.create.call_args
        messages = call_args.kwargs.get("messages") or call_args[1].get("messages")
        prompt_text = messages[0]["content"][0]["text"]
        assert "sampled accessibility tree" in prompt_text

    def test_author_without_aria_has_no_aria_context(self, tmp_path: Path) -> None:
        from harness.intent_extract import author_task

        evidence_dir = _create_evidence_dir(tmp_path, has_aria=False)
        output_path = tmp_path / "output" / "task.yaml"

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = VALID_TASK_YAML

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response

        with patch("harness.intent_extract.OpenAI", return_value=mock_client):
            author_task(evidence_dir, output_path)

        call_args = mock_client.chat.completions.create.call_args
        messages = call_args.kwargs.get("messages") or call_args[1].get("messages")
        prompt_text = messages[0]["content"][0]["text"]
        assert "accessibility tree" not in prompt_text


# ---------------------------------------------------------------------------
# _truncate_aria
# ---------------------------------------------------------------------------


class TestTruncateAria:
    def test_short_text_unchanged(self) -> None:
        text = "AXApplication 'TextEdit'"
        assert _truncate_aria(text, max_chars=100) == text

    def test_long_text_truncated(self) -> None:
        text = "A" * 3000
        result = _truncate_aria(text, max_chars=50)
        assert len(result) < 80  # 50 + ellipsis line
        assert result.endswith("… (truncated)")
        assert result.startswith("A" * 50)

    def test_exact_limit_not_truncated(self) -> None:
        text = "X" * 100
        assert _truncate_aria(text, max_chars=100) == text


# ---------------------------------------------------------------------------
# load_sampled_aria
# ---------------------------------------------------------------------------


def _create_evidence_with_aria_frames(
    tmp_path: Path, num_frames: int, content_fn: object = None
) -> Path:
    """Create an evidence directory with a given number of ARIA frames."""
    evidence_dir = tmp_path / "evidence"
    aria_dir = evidence_dir / "aria"
    aria_dir.mkdir(parents=True)
    for i in range(1, num_frames + 1):
        text = content_fn(i) if content_fn else f'AXApplication "App" frame={i}'
        (aria_dir / f"{i:04d}.yaml").write_text(text)
    return evidence_dir


class TestLoadSampledAria:
    def test_no_aria_dir_returns_empty(self, tmp_path: Path) -> None:
        assert load_sampled_aria(tmp_path) == []

    def test_empty_aria_dir_returns_empty(self, tmp_path: Path) -> None:
        (tmp_path / "aria").mkdir()
        assert load_sampled_aria(tmp_path) == []

    def test_loads_all_when_under_limit(self, tmp_path: Path) -> None:
        evidence = _create_evidence_with_aria_frames(tmp_path, 3)
        result = load_sampled_aria(evidence, max_samples=5)
        assert len(result) == 3
        assert result[0][0] == 1
        assert result[-1][0] == 3
        assert "frame=1" in result[0][1]

    def test_samples_evenly_when_over_limit(self, tmp_path: Path) -> None:
        evidence = _create_evidence_with_aria_frames(tmp_path, 20)
        result = load_sampled_aria(evidence, max_samples=3)
        assert len(result) == 3
        # First and last should be included
        assert result[0][0] == 1
        assert result[-1][0] == 20

    def test_truncates_long_snapshots(self, tmp_path: Path) -> None:
        evidence = _create_evidence_with_aria_frames(tmp_path, 2, content_fn=lambda i: "X" * 5000)
        result = load_sampled_aria(evidence, max_samples=5, max_chars_per_sample=100)
        assert len(result) == 2
        for _, text in result:
            assert len(text) < 150  # 100 + ellipsis

    def test_skips_empty_snapshots(self, tmp_path: Path) -> None:
        evidence_dir = tmp_path / "evidence"
        aria_dir = evidence_dir / "aria"
        aria_dir.mkdir(parents=True)
        (aria_dir / "0001.yaml").write_text("AXApp frame=1")
        (aria_dir / "0002.yaml").write_text("")  # empty
        (aria_dir / "0003.yaml").write_text("AXApp frame=3")
        result = load_sampled_aria(evidence_dir, max_samples=5)
        assert len(result) == 2
        assert result[0][0] == 1
        assert result[1][0] == 3

    def test_skips_whitespace_only_snapshots(self, tmp_path: Path) -> None:
        evidence_dir = tmp_path / "evidence"
        aria_dir = evidence_dir / "aria"
        aria_dir.mkdir(parents=True)
        (aria_dir / "0001.yaml").write_text("AXApp frame=1")
        (aria_dir / "0002.yaml").write_text("   \n  \n  ")  # whitespace only
        result = load_sampled_aria(evidence_dir, max_samples=5)
        assert len(result) == 1
        assert result[0][0] == 1


# ---------------------------------------------------------------------------
# build_prompt with aria_samples
# ---------------------------------------------------------------------------


class TestBuildPromptSampledAria:
    def test_sampled_aria_in_prompt(self) -> None:
        manifest = {"capture_interval_ms": 2000}
        samples = [(1, "AXApp first"), (5, "AXApp middle"), (10, "AXApp last")]
        prompt = build_prompt(manifest, aria_samples=samples)
        assert "sampled accessibility tree" in prompt
        assert "[Frame 1]" in prompt
        assert "[Frame 5]" in prompt
        assert "[Frame 10]" in prompt
        assert "AXApp first" in prompt
        assert "AXApp last" in prompt

    def test_sampled_aria_takes_precedence_over_first_last(self) -> None:
        manifest = {"capture_interval_ms": 2000}
        samples = [(1, "Sampled content")]
        prompt = build_prompt(
            manifest,
            aria_first="SHOULD NOT APPEAR",
            aria_last="SHOULD NOT APPEAR",
            aria_samples=samples,
        )
        assert "Sampled content" in prompt
        assert "SHOULD NOT APPEAR" not in prompt

    def test_falls_back_to_first_last_when_no_samples(self) -> None:
        manifest = {"capture_interval_ms": 2000}
        prompt = build_prompt(manifest, aria_first="AXApp", aria_last="AXWindow")
        assert "First frame accessibility tree" in prompt
        assert "AXApp" in prompt

    def test_no_aria_at_all(self) -> None:
        manifest = {"capture_interval_ms": 2000}
        prompt = build_prompt(manifest)
        assert "accessibility tree" not in prompt


# ---------------------------------------------------------------------------
# build_aligned_events_context
# ---------------------------------------------------------------------------


class TestBuildAlignedEventsContext:
    def test_empty_timeline(self) -> None:
        assert build_aligned_events_context([]) == ""

    def test_click_entry(self) -> None:
        timeline = [
            {
                "t": 1.2,
                "epoch": 1000.0,
                "trigger": "click",
                "screenshot": "0001_1000.png",
                "event": {"type": "mouse", "button": "left", "x": 500, "y": 300},
                "app_context": {"app": "TextEdit"},
            },
        ]
        ctx = build_aligned_events_context(timeline)
        assert "Click at (500, 300)" in ctx
        assert "in TextEdit" in ctx
        assert "Screenshot #1" in ctx

    def test_focus_change_entry(self) -> None:
        timeline = [
            {
                "t": 3.0,
                "epoch": 1003.0,
                "trigger": "focus_change",
                "screenshot": "0002_1003.png",
                "event": {"type": "focus", "from_app": "TextEdit", "to_app": "Safari"},
            },
        ]
        ctx = build_aligned_events_context(timeline)
        assert "App switch from TextEdit to Safari" in ctx
        assert "Screenshot #1" in ctx

    def test_interval_entry(self) -> None:
        timeline = [
            {
                "t": 0.0,
                "epoch": 1000.0,
                "trigger": "interval",
                "screenshot": "0001_1000.png",
                "app_context": {"app": "Safari"},
            },
        ]
        ctx = build_aligned_events_context(timeline)
        assert "Periodic capture" in ctx
        assert "in Safari" in ctx

    def test_screenshot_numbering_sequential(self) -> None:
        timeline = [
            {"t": 0.0, "epoch": 1000.0, "trigger": "interval", "screenshot": "0001.png"},
            {
                "t": 1.0,
                "epoch": 1001.0,
                "trigger": "click",
                "screenshot": "0002.png",
                "event": {"type": "mouse", "x": 0, "y": 0},
            },
            {"t": 2.0, "epoch": 1002.0, "trigger": "interval", "screenshot": "0003.png"},
        ]
        ctx = build_aligned_events_context(timeline)
        assert "Screenshot #1" in ctx
        assert "Screenshot #2" in ctx
        assert "Screenshot #3" in ctx

    def test_ground_truth_framing(self) -> None:
        timeline = [
            {"t": 0.0, "epoch": 1000.0, "trigger": "interval", "screenshot": "0001.png"},
        ]
        ctx = build_aligned_events_context(timeline)
        assert "ground truth" in ctx.lower()


# ---------------------------------------------------------------------------
# select_aligned_screenshots
# ---------------------------------------------------------------------------


class TestSelectAlignedScreenshots:
    def test_selects_referenced_screenshots(self, tmp_path: Path) -> None:
        screenshots_dir = tmp_path / "screenshots"
        screenshots_dir.mkdir()
        (screenshots_dir / "0001_1000.png").write_bytes(b"\x89PNG")
        (screenshots_dir / "0002_1002.png").write_bytes(b"\x89PNG")
        (screenshots_dir / "0003_1004.png").write_bytes(b"\x89PNG")

        timeline = [
            {"t": 0.0, "screenshot": "0001_1000.png"},
            {"t": 2.0, "screenshot": "0003_1004.png"},
        ]
        result = select_aligned_screenshots(screenshots_dir, timeline)
        assert len(result) == 2
        assert result[0].name == "0001_1000.png"
        assert result[1].name == "0003_1004.png"

    def test_deduplicates_references(self, tmp_path: Path) -> None:
        screenshots_dir = tmp_path / "screenshots"
        screenshots_dir.mkdir()
        (screenshots_dir / "0001_1000.png").write_bytes(b"\x89PNG")

        timeline = [
            {"t": 0.0, "screenshot": "0001_1000.png"},
            {"t": 1.0, "screenshot": "0001_1000.png"},
        ]
        result = select_aligned_screenshots(screenshots_dir, timeline)
        assert len(result) == 1

    def test_skips_missing_files(self, tmp_path: Path) -> None:
        screenshots_dir = tmp_path / "screenshots"
        screenshots_dir.mkdir()
        (screenshots_dir / "0001_1000.png").write_bytes(b"\x89PNG")

        timeline = [
            {"t": 0.0, "screenshot": "0001_1000.png"},
            {"t": 1.0, "screenshot": "0099_missing.png"},
        ]
        result = select_aligned_screenshots(screenshots_dir, timeline)
        assert len(result) == 1

    def test_samples_when_over_limit(self, tmp_path: Path) -> None:
        screenshots_dir = tmp_path / "screenshots"
        screenshots_dir.mkdir()
        timeline = []
        for i in range(20):
            name = f"{i:04d}_1000.png"
            (screenshots_dir / name).write_bytes(b"\x89PNG")
            timeline.append({"t": float(i), "screenshot": name})

        result = select_aligned_screenshots(screenshots_dir, timeline, max_frames=5)
        assert len(result) <= 5
        # First and last should be preserved by sample_frames
        assert result[0].name == "0000_1000.png"
        assert result[-1].name == "0019_1000.png"

    def test_empty_timeline(self, tmp_path: Path) -> None:
        screenshots_dir = tmp_path / "screenshots"
        screenshots_dir.mkdir()
        result = select_aligned_screenshots(screenshots_dir, [])
        assert result == []


# ---------------------------------------------------------------------------
# extract_intent with aligned timeline (integration, mocked OpenAI)
# ---------------------------------------------------------------------------


class TestExtractIntentAligned:
    def test_aligned_evidence_uses_timeline_context(self, tmp_path: Path) -> None:
        """When manifest has aligned_timeline, extract_intent uses aligned context."""
        from harness.intent_extract import author_task

        evidence_dir = _create_evidence_dir(tmp_path, num_frames=3)
        # Add aligned_timeline to manifest
        manifest_path = evidence_dir / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["capture_mode"] = "aligned"
        manifest["aligned_timeline"] = [
            {
                "t": 0.0,
                "epoch": 1002.0,
                "trigger": "interval",
                "screenshot": "0001_1002.png",
                "app_context": {"app": "TextEdit"},
            },
            {
                "t": 1.5,
                "epoch": 1003.5,
                "trigger": "click",
                "screenshot": "0002_1004.png",
                "event": {"type": "mouse", "x": 100, "y": 200},
                "app_context": {"app": "TextEdit"},
            },
            {
                "t": 4.0,
                "epoch": 1006.0,
                "trigger": "interval",
                "screenshot": "0003_1006.png",
                "app_context": {"app": "TextEdit"},
            },
        ]
        manifest_path.write_text(json.dumps(manifest))

        output_path = tmp_path / "output" / "task.yaml"

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = VALID_TASK_YAML

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response

        with patch("harness.intent_extract.OpenAI", return_value=mock_client):
            author_task(evidence_dir, output_path)

        call_args = mock_client.chat.completions.create.call_args
        messages = call_args.kwargs.get("messages") or call_args[1].get("messages")
        prompt_text = messages[0]["content"][0]["text"]
        # Aligned context should be present
        assert "Click at (100, 200)" in prompt_text
        assert "in TextEdit" in prompt_text
        assert "Screenshot #" in prompt_text

    def test_non_aligned_evidence_uses_standard_path(self, tmp_path: Path) -> None:
        """Without aligned_timeline, extract_intent uses standard event grouping."""
        from harness.intent_extract import author_task

        evidence_dir = _create_evidence_dir(tmp_path, num_frames=3)
        # Add events.json
        events_data = {
            "capture_start_epoch": 1000.0,
            "events": [
                {"t": 1.0, "type": "mouse", "button": "left", "x": 50, "y": 60, "click_count": 1},
            ],
        }
        (evidence_dir / "events.json").write_text(json.dumps(events_data))

        output_path = tmp_path / "output" / "task.yaml"

        mock_response = MagicMock()
        mock_response.choices = [MagicMock()]
        mock_response.choices[0].message.content = VALID_TASK_YAML

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response

        with patch("harness.intent_extract.OpenAI", return_value=mock_client):
            author_task(evidence_dir, output_path)

        call_args = mock_client.chat.completions.create.call_args
        messages = call_args.kwargs.get("messages") or call_args[1].get("messages")
        prompt_text = messages[0]["content"][0]["text"]
        # Standard events context
        assert "clicked (50, 60)" in prompt_text
        # No aligned markers
        assert "Screenshot #" not in prompt_text
