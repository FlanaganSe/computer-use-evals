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

from harness.compiler import CompileMetadata, DraftTask

logger = logging.getLogger(__name__)

EXTRACTION_PROMPT = """\
You are analyzing evidence of a user performing a task on their computer. \
Your job is to reconstruct exactly what they did — step by step — and \
produce a task definition that an AI agent could be evaluated against.

You have {num_screenshots} screenshots taken {interval} seconds apart.

{events_context}\
{aria_context}\
{transcript_context}\

CRITICAL INSTRUCTIONS:
1. The input event timeline (if provided) is GROUND TRUTH. It records \
every click, keystroke, and scroll the user actually performed. Do NOT \
guess or hallucinate actions — only describe what the events show.
2. Use screenshots for visual context (which app is open, what UI \
elements are visible) but the events tell you WHAT HAPPENED.
3. The user may have performed a MULTI-STEP workflow spanning multiple \
applications. Account for EVERY phase of the workflow. If the user \
started in a browser, then switched to a native app, describe both parts.
4. If the user did NOT submit a form or complete an action, do NOT claim \
they did. Only describe actions that are evidenced by the events.
5. The goal description must be a DETAILED step-by-step description of \
the entire workflow, not a one-line summary. Each distinct phase should \
be mentioned.

Generate a task definition in this exact YAML schema:

task_id: "<short-kebab-case-id>"
version: "1.0"
environment: "<browser or macos_desktop>"

goal:
  description: |
    <Detailed step-by-step description of the full workflow for human review.
    Each step on its own line. Use {{{{variable}}}} placeholders
    for values that should be parameterizable. Example:
    1. Open {{{{url}}}} in the browser.
    2. Fill in the name field with '{{{{name}}}}'.
    3. Copy the form data.
    4. Open TextEdit and paste the copied data.
    5. Save the file as '{{{{filename}}}}'.>
  agent_brief: |
    <Concise, action-oriented instruction for the AI agent executing this task.
    Focus on WHAT to do, not background context. Example:
    Open {{{{url}}}}, fill name='{{{{name}}}}', copy form data,
    paste into TextEdit, save as '{{{{filename}}}}'.>
  variables:
    <variable_name>:
      type: "<url|string|path>"
      default: "<default value from what the user actually typed>"

preconditions:
  - "<What must be true before starting>"

setup_script: null

verification:
  primary:
    method: "programmatic"
    check: "<file_exists('path') or file_contains('path', 'text') \
or form_submitted('name', 'email') or app_focused('AppName') \
or script_check('path/to/verify.py')>"

cleanup_script: null

Guidelines:
- task_id: descriptive, kebab-cased
- environment: "browser" if web-only, "macos_desktop" if native apps \
are involved (including cross-app workflows)
- Variables: use the exact text the user typed as defaults
- Preconditions: the starting state, not the steps
- Verification: check the final outcome. Use file_contains if the user \
saved a file with specific content. Use file_exists if they created a file. \
Use form_submitted only if they actually submitted a form. \
Use app_focused('AppName') if success means a specific app is focused. \
Use script_check('tasks/<task>/verify.py') for custom verification \
(e.g. checking calendar events, reminders, or other app state via AppleScript). \
ONLY use these exact function names — do not invent new ones.
- If the user copied text and pasted it into a file, verification should \
check that the file contains the pasted text

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


# ---------------------------------------------------------------------------
# Aligned timeline helpers
# ---------------------------------------------------------------------------


def build_aligned_events_context(timeline: list[dict[str, Any]]) -> str:
    """Build an events-context string from an aligned timeline.

    Each line correlates a user action with a numbered screenshot so the
    VLM can reason about exactly what happened at each capture point.
    """
    if not timeline:
        return ""

    # Assign sequential screenshot numbers
    screenshot_refs: dict[str, int] = {}
    idx = 0
    for entry in timeline:
        ref = entry.get("screenshot", "")
        if ref and ref not in screenshot_refs:
            idx += 1
            screenshot_refs[ref] = idx

    lines: list[str] = []
    for entry in timeline:
        t = entry.get("t", 0.0)
        trigger = entry.get("trigger", "unknown")
        ref = entry.get("screenshot", "")
        if not ref or ref not in screenshot_refs:
            continue  # skip entries without a valid screenshot reference
        num = screenshot_refs[ref]

        app_ctx = entry.get("app_context")
        app_label = f" in {app_ctx['app']}" if app_ctx and app_ctx.get("app") else ""

        if trigger == "click":
            ev = entry.get("event", {})
            x, y = ev.get("x", 0), ev.get("y", 0)
            lines.append(f"At t={t}s: Click at ({x}, {y}){app_label} → Screenshot #{num}")
        elif trigger == "focus_change":
            ev = entry.get("event", {})
            from_app = ev.get("from_app", "unknown")
            to_app = ev.get("to_app", "unknown")
            lines.append(f"At t={t}s: App switch from {from_app} to {to_app} → Screenshot #{num}")
        elif trigger == "interval":
            lines.append(f"At t={t}s: Periodic capture{app_label} → Screenshot #{num}")
        else:
            lines.append(f"At t={t}s: {trigger}{app_label} → Screenshot #{num}")

    return (
        "Here is a timeline of the user's actions during the recording, "
        "aligned with the numbered screenshots provided. Each screenshot "
        "number corresponds to the image in that position in the image "
        "sequence below. This is ground truth — use this alignment to "
        "understand exactly what the user was doing at each capture point:\n\n"
        + "\n".join(lines)
        + "\n\n"
    )


def select_aligned_screenshots(
    screenshots_dir: Path,
    timeline: list[dict[str, Any]],
    max_frames: int = 10,
) -> list[Path]:
    """Select screenshots referenced in the aligned timeline.

    Preserves timeline order.  If there are more references than
    *max_frames*, evenly samples while keeping first and last.
    """
    seen: set[str] = set()
    unique_refs: list[str] = []
    for entry in timeline:
        ref = entry.get("screenshot", "")
        if ref and ref not in seen:
            seen.add(ref)
            unique_refs.append(ref)

    paths = [screenshots_dir / r for r in unique_refs if (screenshots_dir / r).exists()]
    if len(paths) > max_frames:
        paths = sample_frames(paths, max_frames=max_frames)
    return paths


_MAX_ARIA_SAMPLES = 5
_MAX_ARIA_CHARS = 2000


def _truncate_aria(text: str, max_chars: int = _MAX_ARIA_CHARS) -> str:
    """Truncate an AX snapshot to *max_chars*, appending an ellipsis if trimmed."""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n… (truncated)"


def load_sampled_aria(
    evidence_dir: Path,
    max_samples: int = _MAX_ARIA_SAMPLES,
    max_chars_per_sample: int = _MAX_ARIA_CHARS,
) -> list[tuple[int, str]]:
    """Load and evenly sample AX snapshots from the aria/ directory.

    Returns a list of ``(frame_number, truncated_text)`` tuples.
    If fewer snapshots exist than *max_samples*, all are returned.
    """
    aria_dir = evidence_dir / "aria"
    if not aria_dir.is_dir():
        return []
    aria_files = sorted(aria_dir.glob("*.yaml"))
    if not aria_files:
        return []

    # Re-use the same even-sampling logic used for screenshots
    sampled_files = sample_frames(aria_files, max_frames=max_samples)

    results: list[tuple[int, str]] = []
    for path in sampled_files:
        # Extract sequence number from filename (e.g. "0001.yaml" or "0001_ts.yaml")
        try:
            frame_num = int(path.stem.split("_")[0])
        except ValueError:
            frame_num = 0
        raw = path.read_text()
        if not raw.strip():
            continue
        text = _truncate_aria(raw, max_chars_per_sample)
        results.append((frame_num, text))
    return results


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
    aria_samples: list[tuple[int, str]] | None = None,
) -> str:
    """Build the extraction prompt from manifest and optional context.

    If *aria_samples* is provided (list of ``(frame_number, text)`` tuples),
    it takes precedence over the legacy *aria_first*/*aria_last* parameters,
    giving the model evenly-spaced AX context across the recording.
    """
    interval = int(manifest.get("capture_interval_ms", 2000)) / 1000

    aria_context = ""
    if aria_samples:
        parts = [f"[Frame {frame}] Accessibility state:\n{text}" for frame, text in aria_samples]
        aria_context = (
            "Additionally, here are sampled accessibility tree snapshots "
            "showing UI state at different points during the recording:\n\n"
            + "\n\n".join(parts)
            + "\n\n"
        )
    elif aria_first or aria_last:
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

    When the manifest contains an ``aligned_timeline`` (from aligned
    capture mode), screenshots and events context are derived from
    that timeline for tighter correlation. Otherwise falls back to
    the standard evenly-sampled approach.

    Requires OPENAI_API_KEY in environment.
    """
    manifest = load_evidence(evidence_dir)

    screenshots_dir = evidence_dir / "screenshots"
    all_screenshots = sorted(screenshots_dir.glob("*.png"))
    if not all_screenshots:
        msg = f"No screenshots found in {screenshots_dir}"
        raise FileNotFoundError(msg)

    raw_timeline = manifest.get("aligned_timeline")
    aligned_timeline: list[dict[str, Any]] | None = (
        raw_timeline if isinstance(raw_timeline, list) else None
    )

    if aligned_timeline:
        # Aligned mode: use timeline-correlated screenshots and context
        sampled = select_aligned_screenshots(screenshots_dir, aligned_timeline)
        if not sampled:
            sampled = sample_frames(all_screenshots)
        # Filter timeline to entries whose screenshots are in the sampled set
        # so prompt screenshot numbers match the actual image sequence
        sampled_names = {p.name for p in sampled}
        visible_timeline = [e for e in aligned_timeline if e.get("screenshot") in sampled_names]
        events_context: str | None = build_aligned_events_context(visible_timeline) or None
    else:
        # Standard mode: evenly sample screenshots, group events separately
        sampled = sample_frames(all_screenshots)
        events_context = None
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

    aria_samples: list[tuple[int, str]] | None = None
    if manifest.get("has_aria"):
        aria_samples = load_sampled_aria(evidence_dir) or None

    transcript: str | None = None
    transcript_path = evidence_dir / "transcript.txt"
    if transcript_path.exists():
        transcript = transcript_path.read_text()

    prompt_text = build_prompt(
        manifest,
        transcript=transcript,
        events_context=events_context,
        num_screenshots=len(sampled),
        aria_samples=aria_samples,
    )
    messages = build_messages(prompt_text, sampled)

    client = OpenAI()
    response = client.chat.completions.create(
        model=model,
        messages=messages,  # type: ignore[arg-type]
        max_completion_tokens=2000,
    )
    if not response.choices:
        msg = "VLM returned no choices — possible content filter refusal"
        raise RuntimeError(msg)
    return response.choices[0].message.content or ""


def parse_draft_task(raw_yaml: str) -> DraftTask:
    """Parse VLM output into a DraftTask model. Raises on validation error."""
    cleaned = raw_yaml.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"```(?:yaml)?\s*\n?", "", cleaned)
        cleaned = cleaned.rstrip("`").strip()
    data = yaml.safe_load(cleaned)
    return DraftTask.model_validate(data)


def author_task(
    evidence_dir: Path,
    output_path: Path,
    model: str = "gpt-5.4",
    dry_run: bool = False,
) -> str:
    """Full authoring pipeline: extract intent, parse, write draft YAML.

    Produces a **draft** artifact (not a trusted runtime task).  Run
    ``harness compile`` on the output to validate and produce the final
    runnable ``task.yaml``.

    Returns the draft YAML text (written to output_path unless dry_run).
    """
    from datetime import UTC, datetime

    raw_yaml = extract_intent(evidence_dir, model=model)

    try:
        draft = parse_draft_task(raw_yaml)
        # Attach compile metadata for provenance
        draft.compile_metadata = CompileMetadata(
            source_evidence=str(evidence_dir),
            authoring_model=model,
            authored_at=datetime.now(tz=UTC).isoformat(),
        )
        final_yaml = yaml.dump(
            draft.model_dump(by_alias=True, exclude_none=True),
            default_flow_style=False,
            sort_keys=False,
        )
    except Exception:
        logger.warning("VLM output did not validate against draft schema, saving raw output")
        final_yaml = raw_yaml
        if not dry_run:
            raw_path = output_path.with_suffix(".yaml.raw")
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            raw_path.write_text(raw_yaml)

    if not dry_run:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(final_yaml)
        logger.info("Draft written to %s", output_path)

    return final_yaml
