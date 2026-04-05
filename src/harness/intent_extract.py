"""VLM-based intent extraction and task YAML generation.

Reads an evidence directory (screenshots + optional ARIA/transcript),
sends sampled frames to a vision-language model, and parses the response
into a draft Task YAML.
"""

from __future__ import annotations

import base64
import json
import logging
import re
from pathlib import Path
from typing import Any

import yaml
from openai import OpenAI

from harness.types import Task

logger = logging.getLogger(__name__)

EXTRACTION_PROMPT = """\
You are analyzing evidence of a user performing a task on their computer. \
Your job is to figure out exactly what they did and produce a task \
definition that an AI agent could be evaluated against.

You have {num_screenshots} screenshots taken {interval} seconds apart.

{events_context}\
{aria_context}\
{transcript_context}\

IMPORTANT: Base your analysis on ALL available evidence. \
If an input event timeline is provided, it is the ground truth for what \
the user typed, clicked, and scrolled — do NOT guess or hallucinate \
actions that contradict it. Use the screenshots for visual context \
(which app is open, what UI elements are visible, spatial layout) but \
rely on the event timeline for the actual sequence of user actions.

Do NOT invent actions, websites, or form fields that are not clearly \
visible in the screenshots or recorded in the events.

Generate a task definition in this exact YAML schema:

task_id: "<short-kebab-case-id>"
version: "1.0"
environment: "<browser or macos_desktop>"

goal:
  description: "<Clear description of what to accomplish. \
Use {{{{variable}}}} placeholders for values that should be parameterizable.>"
  variables:
    <variable_name>:
      type: "<url|string|path>"
      default: "<default value>"

preconditions:
  - "<What must be true before starting>"

setup_script: null

verification:
  primary:
    method: "programmatic"
    check: "<file_exists('path') or file_contains('path', 'text') \
or form_submitted('name', 'email')>"

cleanup_script: null

Guidelines:
- The task_id should be descriptive and kebab-cased
- Use variables for any value that might change between runs
- The environment should be "browser" if web-based, "macos_desktop" if native apps. \
If the user switches between browser and native apps, use "macos_desktop".
- Preconditions describe the starting state, not the steps
- Verification checks the outcome, not the path taken
- Available checks: file_exists('path'), file_contains('path', 'text'), \
form_submitted('name', 'email')
- If you cannot determine verification, use a descriptive comment
- If the user typed specific text, use those exact strings as variable defaults

Return ONLY the YAML. No explanation, no markdown code blocks, just raw YAML.\
"""


def load_evidence(evidence_dir: Path) -> dict[str, Any]:
    """Load manifest and evidence metadata from an evidence directory."""
    manifest_path = evidence_dir / "manifest.json"
    if not manifest_path.exists():
        msg = f"No manifest.json found in {evidence_dir}"
        raise FileNotFoundError(msg)
    manifest: dict[str, Any] = json.loads(manifest_path.read_text())
    return manifest


def load_events(evidence_dir: Path) -> list[dict[str, Any]] | None:
    """Load events from events.json if it exists."""
    events_path = evidence_dir / "events.json"
    if not events_path.exists():
        return None
    data: dict[str, Any] = json.loads(events_path.read_text())
    events: list[dict[str, Any]] = data.get("events", [])
    return events if events else None


# Max gap between keystrokes to group them as a single "typed" string
_KEYSTROKE_GROUP_GAP = 0.5


def group_events(events: list[dict[str, Any]]) -> list[str]:
    """Group raw events into human-readable action descriptions.

    Sequential printable key events within 500ms are grouped into typed strings.
    """
    if not events:
        return []

    descriptions: list[str] = []
    pending_chars: list[str] = []
    pending_start: float = 0.0
    prev_t: float = 0.0

    def _flush_pending() -> None:
        if pending_chars:
            text = "".join(pending_chars)
            descriptions.append(f"At t={pending_start}s typed '{text}'")
            pending_chars.clear()

    for evt in events:
        t: float = evt.get("t", 0.0)
        evt_type: str = evt.get("type", "")

        if evt_type == "key":
            char = evt.get("char")
            modifiers: list[str] = evt.get("modifiers", [])
            key_name = evt.get("key_name")
            hotkey_mods = set(modifiers) & {"command", "control", "option"}

            # Printable char with no hotkey modifiers → group
            if char is not None and not hotkey_mods:
                if pending_chars and (t - prev_t) > _KEYSTROKE_GROUP_GAP:
                    _flush_pending()
                if not pending_chars:
                    pending_start = t
                pending_chars.append(char)
                prev_t = t
                continue

            # Non-groupable key event — flush pending first
            _flush_pending()
            if hotkey_mods:
                mod_str = "+".join(m.capitalize() for m in modifiers)
                key_label = char if char else (key_name or f"keycode={evt.get('keycode')}")
                descriptions.append(f"At t={t}s pressed {mod_str}+{key_label}")
            elif key_name:
                descriptions.append(f"At t={t}s pressed [{key_name}]")
            else:
                descriptions.append(f"At t={t}s pressed keycode={evt.get('keycode')}")

        elif evt_type == "mouse":
            _flush_pending()
            button = evt.get("button", "left")
            x, y = evt.get("x", 0), evt.get("y", 0)
            click_count = evt.get("click_count", 1)
            if click_count >= 2:
                descriptions.append(f"At t={t}s double-clicked ({x}, {y})")
            elif button == "right":
                descriptions.append(f"At t={t}s right-clicked ({x}, {y})")
            else:
                descriptions.append(f"At t={t}s clicked ({x}, {y})")

        elif evt_type == "scroll":
            _flush_pending()
            delta_y = evt.get("delta_y", 0)
            direction = "up" if delta_y > 0 else "down"
            descriptions.append(f"At t={t}s scrolled {direction}")

    _flush_pending()
    return descriptions


def sample_frames(screenshots: list[Path], max_frames: int = 10) -> list[Path]:
    """Sample frames evenly across the recording."""
    if not screenshots or max_frames <= 0:
        return screenshots
    if len(screenshots) <= max_frames:
        return screenshots
    if max_frames == 1:
        return [screenshots[0]]
    indices: list[int] = [0, len(screenshots) - 1]
    step = (len(screenshots) - 1) / (max_frames - 1)
    for i in range(1, max_frames - 1):
        indices.append(int(i * step))
    indices = sorted(set(indices))
    return [screenshots[i] for i in indices]


def build_prompt(
    manifest: dict[str, Any],
    aria_first: str | None = None,
    aria_last: str | None = None,
    transcript: str | None = None,
    events_context: str | None = None,
    num_screenshots: int = 0,
) -> str:
    """Build the extraction prompt from manifest and optional context."""
    interval = int(manifest.get("capture_interval_ms", 2000)) / 1000

    aria_context = ""
    if aria_first or aria_last:
        parts = []
        if aria_first:
            parts.append(f"First frame accessibility tree:\n{aria_first}")
        if aria_last:
            parts.append(f"Last frame accessibility tree:\n{aria_last}")
        aria_context = (
            "Additionally, here are accessibility tree snapshots:\n\n"
            + "\n\n".join(parts)
            + "\n\n"
        )

    transcript_context = ""
    if transcript:
        transcript_context = (
            f"The user provided voice narration during the recording:\n\n{transcript}\n\n"
        )

    return EXTRACTION_PROMPT.format(
        interval=interval,
        num_screenshots=num_screenshots,
        aria_context=aria_context,
        events_context=events_context or "",
        transcript_context=transcript_context,
    )


def build_messages(
    prompt_text: str,
    screenshots: list[Path],
) -> list[dict[str, Any]]:
    """Build OpenAI chat messages with text prompt and base64-encoded images."""
    content: list[dict[str, Any]] = [{"type": "text", "text": prompt_text}]

    for screenshot in screenshots:
        image_data = base64.b64encode(screenshot.read_bytes()).decode("utf-8")
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{image_data}"},
            }
        )

    return [{"role": "user", "content": content}]


def extract_intent(
    evidence_dir: Path,
    model: str = "gpt-5.4",
) -> str:
    """Send evidence to a VLM and get back raw YAML text.

    Requires OPENAI_API_KEY in environment.
    """
    manifest = load_evidence(evidence_dir)

    screenshots_dir = evidence_dir / "screenshots"
    all_screenshots = sorted(screenshots_dir.glob("*.png"))
    if not all_screenshots:
        msg = f"No screenshots found in {screenshots_dir}"
        raise FileNotFoundError(msg)
    sampled = sample_frames(all_screenshots)

    aria_first: str | None = None
    aria_last: str | None = None
    if manifest.get("has_aria"):
        aria_dir = evidence_dir / "aria"
        aria_files = sorted(aria_dir.glob("*.yaml"))
        if aria_files:
            aria_first = aria_files[0].read_text()
        if len(aria_files) > 1:
            aria_last = aria_files[-1].read_text()

    transcript: str | None = None
    transcript_path = evidence_dir / "transcript.txt"
    if transcript_path.exists():
        transcript = transcript_path.read_text()

    events_context: str | None = None
    events = load_events(evidence_dir)
    if events:
        grouped = group_events(events)
        if grouped:
            events_context = (
                "Here is the exact timeline of the user's keyboard, mouse, "
                "and scroll input during the recording. This is ground truth — "
                "these are the actual actions the user performed:\n\n"
                + "\n".join(grouped)
                + "\n\n"
            )

    prompt_text = build_prompt(
        manifest, aria_first, aria_last, transcript, events_context, len(sampled)
    )
    messages = build_messages(prompt_text, sampled)

    client = OpenAI()
    response = client.chat.completions.create(
        model=model,
        messages=messages,  # type: ignore[arg-type]
        max_tokens=2000,
    )
    if not response.choices:
        msg = "VLM returned no choices — possible content filter refusal"
        raise RuntimeError(msg)
    return response.choices[0].message.content or ""


def parse_draft_task(raw_yaml: str) -> Task:
    """Parse VLM output into a Task model. Raises on validation error."""
    cleaned = raw_yaml.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"```(?:yaml)?\s*\n?", "", cleaned)
        cleaned = cleaned.rstrip("`").strip()
    data = yaml.safe_load(cleaned)
    return Task.model_validate(data)


def author_task(
    evidence_dir: Path,
    output_path: Path,
    model: str = "gpt-5.4",
    dry_run: bool = False,
) -> str:
    """Full authoring pipeline: extract intent, parse, write YAML.

    Returns the raw YAML text (written to output_path unless dry_run).
    """
    raw_yaml = extract_intent(evidence_dir, model=model)

    try:
        task = parse_draft_task(raw_yaml)
        final_yaml = yaml.dump(
            task.model_dump(by_alias=True, exclude_none=True),
            default_flow_style=False,
            sort_keys=False,
        )
    except Exception:
        logger.warning("VLM output did not validate against Task schema, saving raw output")
        final_yaml = raw_yaml
        if not dry_run:
            raw_path = output_path.with_suffix(".yaml.raw")
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            raw_path.write_text(raw_yaml)

    if not dry_run:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(final_yaml)
        logger.info("Draft task written to %s", output_path)

    return final_yaml
