"""Tests for capture.py with mocked system calls."""

from __future__ import annotations

import json
import signal
from pathlib import Path
from unittest.mock import MagicMock, patch

from harness.capture import build_manifest, capture_session


class TestBuildManifest:
    def test_basic_manifest(self) -> None:
        frames = [
            {"sequence": 1, "timestamp": 1000},
            {"sequence": 2, "timestamp": 1002},
            {"sequence": 3, "timestamp": 1004},
        ]
        manifest = build_manifest(
            task_name="test-task",
            frames=frames,
            interval_seconds=2.0,
            capture_aria=False,
        )
        assert manifest["task_name"] == "test-task"
        assert manifest["total_frames"] == 3
        assert manifest["duration_seconds"] == 4
        assert manifest["capture_interval_ms"] == 2000
        assert manifest["has_aria"] is False
        assert manifest["has_transcript"] is False
        assert manifest["platform"] == "darwin"

    def test_single_frame_duration(self) -> None:
        frames = [{"sequence": 1, "timestamp": 1000}]
        manifest = build_manifest(
            task_name="one",
            frames=frames,
            interval_seconds=1.0,
            capture_aria=True,
        )
        assert manifest["duration_seconds"] == 0
        assert manifest["has_aria"] is True


class TestCaptureSession:
    def test_captures_frames_and_writes_manifest(self, tmp_path: Path) -> None:
        call_count = 0

        def fake_screencapture(args: list[str], **kwargs: object) -> MagicMock:
            nonlocal call_count
            call_count += 1
            # Create a fake screenshot file
            path = Path(args[2])
            path.write_bytes(b"\x89PNG fake")
            result = MagicMock()
            result.returncode = 0
            return result

        def fake_sleep(seconds: float) -> None:
            # After capturing 3 frames, simulate Ctrl+C by sending SIGINT
            if call_count >= 3:
                signal.raise_signal(signal.SIGINT)

        with (
            patch("subprocess.run", side_effect=fake_screencapture),
            patch("time.sleep", side_effect=fake_sleep),
        ):
            result = capture_session(
                output_dir=tmp_path,
                interval_seconds=0.1,
                task_name="test-capture",
            )

        assert result == tmp_path
        assert (tmp_path / "screenshots").is_dir()
        assert (tmp_path / "manifest.json").exists()

        manifest = json.loads((tmp_path / "manifest.json").read_text())
        assert manifest["task_name"] == "test-capture"
        assert manifest["total_frames"] == 3
        assert len(list((tmp_path / "screenshots").glob("*.png"))) == 3

    def test_screencapture_failure_stops_gracefully(self, tmp_path: Path) -> None:
        """If screencapture fails on first frame, capture stops with 0 frames."""
        import subprocess as sp

        with patch("subprocess.run", side_effect=sp.CalledProcessError(1, "screencapture")):
            result = capture_session(output_dir=tmp_path, task_name="fail-test")

        assert result == tmp_path
        manifest = json.loads((tmp_path / "manifest.json").read_text())
        assert manifest["total_frames"] == 0

    def test_aria_capture_creates_aria_dir(self, tmp_path: Path) -> None:
        call_count = 0

        def fake_screencapture(args: list[str], **kwargs: object) -> MagicMock:
            nonlocal call_count
            call_count += 1
            Path(args[2]).write_bytes(b"\x89PNG fake")
            result = MagicMock()
            result.returncode = 0
            return result

        def fake_sleep(seconds: float) -> None:
            if call_count >= 1:
                signal.raise_signal(signal.SIGINT)

        with (
            patch("subprocess.run", side_effect=fake_screencapture),
            patch("time.sleep", side_effect=fake_sleep),
            patch(
                "harness.capture._capture_focused_app_aria",
                return_value='AXApplication "TestApp"',
            ),
        ):
            capture_session(
                output_dir=tmp_path,
                interval_seconds=0.1,
                capture_aria=True,
                task_name="aria-test",
            )

        assert (tmp_path / "aria").is_dir()
        aria_files = list((tmp_path / "aria").glob("*.yaml"))
        assert len(aria_files) == 1

        manifest = json.loads((tmp_path / "manifest.json").read_text())
        assert manifest["has_aria"] is True

    def test_screenshot_naming_convention(self, tmp_path: Path) -> None:
        """Screenshots follow NNNN_timestamp.png pattern."""
        call_count = 0

        def fake_screencapture(args: list[str], **kwargs: object) -> MagicMock:
            nonlocal call_count
            call_count += 1
            Path(args[2]).write_bytes(b"\x89PNG fake")
            result = MagicMock()
            result.returncode = 0
            return result

        def fake_sleep(seconds: float) -> None:
            if call_count >= 2:
                signal.raise_signal(signal.SIGINT)

        with (
            patch("subprocess.run", side_effect=fake_screencapture),
            patch("time.sleep", side_effect=fake_sleep),
        ):
            capture_session(output_dir=tmp_path, interval_seconds=0.1, task_name="naming")

        screenshots = sorted((tmp_path / "screenshots").glob("*.png"))
        assert len(screenshots) == 2
        # Check naming pattern: 0001_<timestamp>.png
        assert screenshots[0].name.startswith("0001_")
        assert screenshots[1].name.startswith("0002_")
