# M7 Handoff: Add Input Event Capture to Evidence Pipeline

## How to use this prompt

This is a complete handoff for Milestone 7 of the Desktop Agent Eval Harness. M7 adds mouse click, keyboard, and scroll event recording to `harness capture` so that captured evidence includes **what the user did**, not just what the screen looked like.

**Read this fully before writing any code. Understand what you're building and why.**

---

## Why this milestone exists

M6 built the evidence capture pipeline: periodic screenshots → VLM intent extraction → draft task YAML. The user identified a critical gap after M6: **screenshots alone are insufficient for reliable intent extraction.** A frame of a form doesn't tell you what was typed, clicked, or in what order. The VLM is guessing from static images when it should have the actual interaction timeline.

M7 fills that gap by adding passive input event recording alongside the existing screenshot loop. Events supplement screenshots — the VLM still gets visual frames for context, but now also gets a timeline like: "At t=1.2s clicked (500,300), at t=2.5s typed 'jane@example.com', at t=4.0s pressed [Return]."

This is a small, focused change: ~80-100 lines of new code, no new dependencies.

---

## Before you write code: understand the domain

### What CGEventTap is

`CGEventTap` is a macOS Core Graphics API (stable since macOS 10.4, ~20 years) that lets you observe system-wide input events. Think of it as a passive tap on the HID event stream. We use `kCGEventTapOptionListenOnly` which means the tap **cannot** modify, inject, or block events — it is purely an observer. This is the same API used by Hammerspoon, Karabiner, and iTerm2 for event monitoring.

### Why CGEventTap and not NSEvent.addGlobalMonitor

Both require Accessibility permission. The difference:
- **CGEventTap** needs only a `CFRunLoop` — works cleanly in a background daemon thread
- **NSEvent.addGlobalMonitor** needs a full `NSApplication` event loop (`AppHelper.runEventLoop()`) — heavy and fragile for a background capture thread

CGEventTap is the right choice because `capture.py`'s screenshot loop runs in the main thread with `time.sleep()`. The event tap runs in a daemon thread with its own `CFRunLoop`. They're independent.

### What the research already established

Extensive API research was completed before this handoff. **Read `.claude/plans/m7-research.md` thoroughly** — it contains:
- Every API symbol verified available in the project's pyobjc-framework-Quartz
- Tested call patterns for `CGEventTapCreate`, `CGEventKeyboardGetUnicodeString`, event masks
- The exact threading model (daemon thread + CFRunLoop)
- The `kCGEventTapDisabledByTimeout` gotcha and re-enable pattern
- Timestamp correlation strategy (`time.monotonic()` offsets)
- The `events.json` schema
- Keystroke grouping algorithm for VLM prompt construction
- Privacy implications (this is a keylogger — passwords will be captured)
- Full risk assessment

Do not re-research these topics. The findings are verified. Use them as your implementation guide.

### Privacy: this is a keylogger

This is not a metaphor. CGEventTap with `kCGEventKeyDown` records every keystroke system-wide. The `events.json` file **will** contain passwords, tokens, private messages — anything the user types during capture. You must:
1. Print a clear warning when event capture starts: "Recording keyboard and mouse input. Evidence may contain passwords."
2. Never transmit events over the network
3. Degrade gracefully if Accessibility permission is denied (skip events, continue screenshots)

---

## What exists (M1-M6 are done)

### Files you must read before implementing

| File | What to understand |
|---|---|
| `src/harness/capture.py` | **The file you'll modify.** Understand the screenshot loop, signal handler, `build_manifest()`, how `capture_session()` returns. The event tap thread must start before the screenshot loop and stop after it. |
| `src/harness/intent_extract.py` | **The file you'll modify.** Understand `build_prompt()`, `load_evidence()`, and the `EXTRACTION_PROMPT` template. You'll add an `{events_context}` section to the prompt and a function that reads `events.json`, groups keystrokes, and produces a human-readable timeline. |
| `src/harness/cli.py` | **The file you'll modify.** Understand the `capture` subparser. You'll add an `--events/--no-events` flag (default: on). |
| `src/harness/environments/macos.py` | **Reference only.** Understand how pyobjc is used in this project — import patterns, error handling style, the `_check_accessibility_permission()` pattern you can reuse. |
| `.claude/plans/m7-research.md` | **Your implementation guide.** Read sections 2-7 and 10-11 especially. |

### The capture pipeline flow today

```
User runs: harness capture --output evidence/my-task/ --interval 2
    ↓
capture_session() starts:
  - Creates screenshots/ dir (and optionally aria/ dir)
  - Installs SIGINT handler
  - Loop: screencapture → save PNG → optional ARIA → sleep(interval)
  - On Ctrl+C: running = False → exit loop
  - Writes manifest.json
  - Returns evidence dir path
```

After M7:
```
User runs: harness capture --output evidence/my-task/ --interval 2 --events
    ↓
capture_session() starts:
  - Creates screenshots/ dir (and optionally aria/ dir)
  - Starts event tap daemon thread (CGEventTap + CFRunLoop)   ← NEW
  - Installs SIGINT handler
  - Loop: screencapture → save PNG → optional ARIA → sleep(interval)
  - On Ctrl+C: running = False → exit loop
  - Stops event tap thread (CFRunLoopStop + join)               ← NEW
  - Writes events.json from collected events                    ← NEW
  - Writes manifest.json (now includes has_events: true)        ← MODIFIED
  - Returns evidence dir path
```

---

## Concrete changes per file

### 1. `src/harness/capture.py` — Event tap alongside screenshot loop

Add an `EventTap` class or a pair of start/stop functions that manage:
- Creating the CGEventTap (listen-only, session-level)
- Running a daemon thread with its own CFRunLoop
- A callback that extracts event data and appends to a list
- Stopping the run loop and joining the thread on capture stop
- Writing `events.json` to the evidence directory

**The event tap callback** (from research):
```python
def _event_callback(proxy, event_type, event, refcon):
    # Handle tap timeout re-enable
    if event_type == Quartz.kCGEventTapDisabledByTimeout:
        Quartz.CGEventTapEnable(tap_ref, True)
        return event

    t = round(time.monotonic() - capture_start, 3)

    if event_type in (Quartz.kCGEventLeftMouseDown, Quartz.kCGEventRightMouseDown):
        loc = Quartz.CGEventGetLocation(event)
        click_count = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGMouseEventClickState)
        events_list.append({
            "t": t, "type": "mouse",
            "button": "left" if event_type == Quartz.kCGEventLeftMouseDown else "right",
            "x": int(loc.x), "y": int(loc.y), "click_count": int(click_count),
        })

    elif event_type == Quartz.kCGEventKeyDown:
        keycode = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventKeycode)
        actual_len, chars = Quartz.CGEventKeyboardGetUnicodeString(event, 4, None, None)
        flags = Quartz.CGEventGetFlags(event)
        modifiers = _extract_modifiers(flags)
        char = chars if actual_len > 0 and chars.isprintable() and not modifiers else None
        key_name = _SPECIAL_KEYS.get(keycode) if char is None else None
        events_list.append({
            "t": t, "type": "key", "char": char, "keycode": int(keycode),
            **({"key_name": key_name} if key_name else {}),
            "modifiers": modifiers,
        })

    elif event_type == Quartz.kCGEventScrollWheel:
        loc = Quartz.CGEventGetLocation(event)
        delta = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGScrollWheelEventDeltaAxis1)
        events_list.append({
            "t": t, "type": "scroll", "delta_y": int(delta),
            "x": int(loc.x), "y": int(loc.y),
        })

    return event
```

**Modifier extraction helper**:
```python
_MODIFIER_MAP = {
    Quartz.kCGEventFlagMaskShift: "shift",
    Quartz.kCGEventFlagMaskControl: "control",
    Quartz.kCGEventFlagMaskAlternate: "option",
    Quartz.kCGEventFlagMaskCommand: "command",
}

def _extract_modifiers(flags: int) -> list[str]:
    return [name for mask, name in _MODIFIER_MAP.items() if flags & mask]
```

**Special key map**:
```python
_SPECIAL_KEYS = {
    36: "Return", 48: "Tab", 49: "Space", 51: "Delete", 53: "Escape",
    123: "Left", 124: "Right", 125: "Down", 126: "Up",
    76: "Enter", 117: "ForwardDelete", 115: "Home", 119: "End",
    116: "PageUp", 121: "PageDown",
}
```

**Threading lifecycle**:
```python
# Start (before screenshot loop):
tap = Quartz.CGEventTapCreate(
    Quartz.kCGSessionEventTap,
    Quartz.kCGHeadInsertEventTap,
    Quartz.kCGEventTapOptionListenOnly,
    mask,
    _event_callback,
    None,
)
if tap is None:
    logger.warning("CGEventTap creation failed — Accessibility permission likely not granted. Skipping event capture.")
    # Continue without events — screenshots still work
else:
    source = Quartz.CFMachPortCreateRunLoopSource(None, tap, 0)
    thread = threading.Thread(target=_run_tap_thread, args=(tap, source), daemon=True)
    thread.start()

# Stop (after screenshot loop exits):
if loop_ref is not None:
    Quartz.CFRunLoopStop(loop_ref)
    thread.join(timeout=2)
# Write events.json
```

**Key design decisions**:
- `capture_start = time.monotonic()` is set once before the tap starts. All event timestamps are relative offsets from this point. This avoids mach_absolute_time conversion entirely.
- `events_list` is a plain `list`. Python's GIL makes `list.append()` thread-safe for simple appends. No lock needed.
- If `CGEventTapCreate` returns `None` (no Accessibility permission), event capture is silently skipped. Screenshots continue normally. A warning is logged.
- The `capture_events` parameter defaults to `True`.

**Modify `capture_session()` signature**:
```python
def capture_session(
    output_dir: Path,
    interval_seconds: float = 2.0,
    capture_aria: bool = False,
    capture_events: bool = True,  # NEW
    task_name: str = "untitled",
) -> Path:
```

**Modify `build_manifest()`** to accept and include `has_events`:
```python
def build_manifest(
    *,
    task_name: str,
    frames: list[dict[str, int]],
    interval_seconds: float,
    capture_aria: bool,
    has_events: bool = False,  # NEW
    transcript_exists: bool = False,
    notes_exists: bool = False,
) -> dict[str, object]:
```

### 2. `src/harness/intent_extract.py` — Event context in VLM prompt

Add two functions:

**`group_events(events: list[dict]) -> list[str]`** — Groups raw events into human-readable action descriptions. This is the keystroke grouping algorithm from the research:
1. Sequential `key` events with printable `char` and no command/control modifiers, within 500ms of each other, are grouped into "typed 'string'"
2. A click, scroll, pause >500ms, or special key breaks the group
3. Keys with modifiers become "pressed Cmd+C" etc.
4. Mouse becomes "clicked (x, y)" or "right-clicked (x, y)" or "double-clicked (x, y)"
5. Scroll becomes "scrolled down/up"
6. Each action is prefixed with its timestamp: "At t=1.2s typed 'jane@example.com'"

**`load_events(evidence_dir: Path) -> list[dict] | None`** — Reads `events.json` if it exists.

**Modify `build_prompt()`** to accept optional `events_context: str | None` and include it in the prompt template. Add an `{events_context}` placeholder to `EXTRACTION_PROMPT`:

```
{events_context}\
```

Where `events_context` is built like:
```python
events_context = ""
if events:
    grouped = group_events(events)
    if grouped:
        events_context = (
            "Additionally, here is a timeline of the user's input events "
            "during the recording:\n\n"
            + "\n".join(grouped)
            + "\n\n"
        )
```

**Modify `extract_intent()`** to load and pass events.

### 3. `src/harness/cli.py` — Add `--events` flag

Add to the `capture` subparser:
```python
capture_parser.add_argument(
    "--events/--no-events",  # argparse doesn't support this syntax
)
```

Actually, use `argparse` style:
```python
capture_parser.add_argument(
    "--no-events", action="store_true", default=False,
    help="Disable keyboard/mouse event recording",
)
```

In the capture command handler:
```python
capture_events = not args.no_events
if capture_events:
    print("Recording keyboard and mouse input. Evidence may contain passwords.")
```

Pass `capture_events` through to `capture_session()`.

### 4. `manifest.json` — Add `has_events` field

Already handled by `build_manifest()` changes above. The manifest gains:
```json
{
  "has_events": true,
  ...
}
```

---

## The events.json schema

Written to `<evidence_dir>/events.json` when capture stops.

```json
{
  "capture_start_epoch": 1775346544.189,
  "events": [
    {"t": 0.0, "type": "mouse", "button": "left", "x": 500, "y": 300, "click_count": 1},
    {"t": 1.2, "type": "key", "char": "j", "keycode": 38, "modifiers": []},
    {"t": 1.3, "type": "key", "char": null, "keycode": 36, "key_name": "Return", "modifiers": []},
    {"t": 5.7, "type": "key", "char": "c", "keycode": 8, "modifiers": ["command"]},
    {"t": 8.9, "type": "scroll", "delta_y": -3, "x": 640, "y": 400}
  ]
}
```

- `capture_start_epoch`: `time.time()` when capture began (for absolute time correlation with manifest)
- `t`: relative seconds from capture start (float, 3 decimal places)
- `type`: `"mouse"` | `"key"` | `"scroll"`
- `char`: printable character or `null`
- `key_name`: human-readable name for special keys, only present when `char` is `null`
- `keycode`: raw macOS key code (int)
- `modifiers`: list of `"shift"` | `"control"` | `"option"` | `"command"` (empty list = none)
- `button`: `"left"` | `"right"`
- `click_count`: 1 = single, 2 = double
- `x`, `y`: pixel coordinates (int)
- `delta_y`: scroll amount (positive = up, negative = down)

---

## What NOT to do

1. **Do not re-research the CGEventTap API.** The research in `m7-research.md` is verified. Use it.
2. **Do not use `NSEvent.addGlobalMonitor`.** It requires `NSApplication` — too heavy for a background thread.
3. **Do not modify or inject events.** `kCGEventTapOptionListenOnly` is non-negotiable.
4. **Do not add new dependencies.** `pyobjc-framework-Quartz` is already installed.
5. **Do not skip the privacy warning.** "Recording keyboard and mouse input. Evidence may contain passwords."
6. **Do not group keystrokes at capture time.** Store raw events in `events.json`. Grouping happens at prompt construction time in `intent_extract.py`.
7. **Do not use locks for the events list.** `list.append()` is GIL-safe. A lock adds complexity without benefit.
8. **Do not convert mach_absolute_time.** Use `time.monotonic()` offsets — simpler and tested.
9. **Do not block the screenshot loop.** The event tap runs in a daemon thread. The main thread keeps its `time.sleep()` loop unchanged.
10. **Do not make event capture fail loudly.** If `CGEventTapCreate` returns `None`, log a warning and continue with screenshots only.

---

## Implementation order

1. **Read all files listed above.** Understand `capture.py`'s current structure, `intent_extract.py`'s prompt template, `cli.py`'s argument parsing, and the pyobjc patterns in `macos.py`.

2. **Read `.claude/plans/m7-research.md`.** This is your implementation guide. Sections 2-7 and 10-11 map directly to what you're building.

3. **Modify `capture.py`:**
   - Add the event tap functions (callback, modifier extraction, special key map, thread runner)
   - Add `capture_events: bool = True` parameter to `capture_session()`
   - Start the event tap thread before the screenshot loop
   - Stop the thread after the loop exits
   - Write `events.json` to the evidence directory
   - Add `has_events` to `build_manifest()`
   - Handle graceful degradation (tap creation failure → log warning, continue without events)

4. **Modify `intent_extract.py`:**
   - Add `load_events()` to read events.json
   - Add `group_events()` to convert raw events into human-readable action descriptions
   - Add `{events_context}` to the prompt template
   - Modify `build_prompt()` to accept and format events context
   - Modify `extract_intent()` to load and pass events

5. **Modify `cli.py`:**
   - Add `--no-events` flag to the capture subparser
   - Print privacy warning when events are enabled
   - Pass `capture_events` through to `capture_session()`

6. **Write tests:**
   - `tests/test_events.py` — test event grouping logic (pure Python, no OS dependency):
     - Group sequential printable keys into typed strings
     - Break groups on pause >500ms, clicks, scroll, special keys
     - Handle modifier combinations (Cmd+C → "pressed Cmd+C")
     - Handle double-clicks
     - Handle empty event lists
   - Add tests to `tests/test_capture.py` — test that `build_manifest()` includes `has_events`
   - Add tests to `tests/test_intent_extract.py` — test that `build_prompt()` includes events context when provided

7. **Run the full quality suite:**
   ```bash
   uv run pytest -v
   uv run ruff check src/ tests/
   uv run ruff format --check src/ tests/
   uv run mypy src/
   ```

8. **Fix any issues.** If a test, lint, or type check fails 3 times after fixes, stop and report what you've tried.

9. **Update `.claude/plans/plan.md`** — add an M7 section to the milestone outline:
   ```
   - [ ] M7: Input Event Capture for Evidence Pipeline
     - [ ] Step 1 — Add CGEventTap event recorder to capture.py → verify: `uv run mypy src/harness/capture.py`
     - [ ] Step 2 — Add event grouping + prompt integration to intent_extract.py → verify: `uv run mypy src/harness/intent_extract.py`
     - [ ] Step 3 — Add --no-events flag to cli.py → verify: `uv run mypy src/harness/cli.py`
     - [ ] Step 4 — Write tests for event grouping, prompt construction, manifest → verify: `uv run pytest tests/test_events.py tests/test_capture.py tests/test_intent_extract.py -v`
     - [ ] Step 5 — Full quality suite → verify: `uv run pytest -v && uv run ruff check src/ tests/ && uv run mypy src/`
   ```
   Mark each checkbox as you complete it.

10. **Commit.** One-line message. No Co-Authored-By.

---

## Reflect before implementing

Before you write the first line, answer these questions for yourself:

1. **Do you understand why events supplement screenshots rather than replacing them?** The VLM still needs visual frames for spatial context — seeing where buttons are, what app is open, what the UI looks like. Events tell it *what happened between frames*: what was typed, clicked, scrolled. Together they give far richer signal than either alone.

2. **Do you understand the threading model?** The main thread runs the screenshot loop with `time.sleep()`. The event tap runs in a daemon thread with `CFRunLoopRun()`. They don't interact except through a shared `events_list` (thread-safe via GIL). The main thread starts the daemon thread before the loop and stops it after.

3. **Do you understand the graceful degradation contract?** If `CGEventTapCreate` returns `None` (no Accessibility permission), the capture session continues normally with screenshots only. `has_events` is `False` in the manifest. `events.json` is not written. The VLM prompt doesn't include events context. Everything works as it did before M7.

4. **Do you understand where grouping happens?** Raw individual events go into `events.json`. Grouping (keystroke aggregation into typed strings) happens in `intent_extract.py` at prompt construction time. This separation means `events.json` is a complete raw record, and the grouping logic is pure Python that's trivially testable.

5. **Do you understand the `kCGEventTapDisabledByTimeout` gotcha?** If the callback blocks for >~1 second, macOS auto-disables the tap. The callback must check for this event type and re-enable the tap. Our callback is trivial (<1ms), so this is belt-and-suspenders, but you must implement the handler.

---

## After implementation: how the user tests this

```bash
# 1. Run unit tests (no Accessibility permission needed)
uv sync
uv run pytest -v

# 2. Run quality checks
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run mypy src/

# 3. Test capture with events (requires macOS + Accessibility permission)
# Do something on screen — click around, type in a text field, scroll
uv run python -m harness capture --output evidence/test-events/ --interval 2
# Press Ctrl+C after ~10 seconds

# 4. Inspect the evidence
cat evidence/test-events/manifest.json | python -m json.tool
# Should show "has_events": true
cat evidence/test-events/events.json | python -m json.tool
# Should show timestamped mouse, key, scroll events

# 5. Test capture WITHOUT events
uv run python -m harness capture --output evidence/test-no-events/ --interval 2 --no-events
# Press Ctrl+C after a few seconds
# events.json should NOT exist in the output

# 6. Test VLM extraction with events (requires OPENAI_API_KEY)
OPENAI_API_KEY=sk-... uv run python -m harness author evidence/test-events/ --output tasks/test-events/task.yaml
# The draft task should reflect the actions you performed (typed text, clicked locations)

# 7. Verify existing M1-M6 functionality is unbroken
uv run pytest -v
```

---

## Finishing up

When everything passes:

1. Run `ruff format`, full test suite, lint, mypy.
2. Stage and commit. One-line message. No Co-Authored-By.
3. Tell the user:
   - What the event tap captures (mouse clicks, keyboard, scroll — listen-only, passive)
   - The `events.json` schema and how it relates to screenshot timestamps
   - How events appear in the VLM prompt (grouped keystroke strings, click coordinates, scroll actions)
   - The privacy warning behavior
   - The graceful degradation path (no Accessibility permission → screenshots only)
   - The testing commands above
   - Any surprises or edge cases you encountered during implementation

---

## Notes on intent

M7 is the difference between "here are 15 screenshots of someone using a form" and "here are 15 screenshots of someone using a form, and between frames 3 and 7 they typed 'jane@example.com' into a text field, then clicked Submit at (800, 450)."

The VLM goes from guessing to knowing. That's the value of this milestone.
