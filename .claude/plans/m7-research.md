# M7 Research: macOS Input Event Capture via CGEventTap

## Summary

CGEventTap via pyobjc-framework-Quartz is the right tool. All required APIs are verified available in the project's venv. The approach is low-risk: ~80-100 lines of new code, no new dependencies, well-understood Apple API with 20+ years of stability.

---

## 1. API Availability — Verified

All symbols confirmed present in the project's pyobjc-framework-Quartz (Python 3.13, pyobjc >=10.0):

### Core functions
| Function | Purpose |
|---|---|
| `CGEventTapCreate` | Create passive event listener |
| `CGEventTapEnable` | Enable/disable tap (and re-enable on timeout) |
| `CGEventGetType` | Event type classification |
| `CGEventGetLocation` | Mouse coordinates (CGPoint with .x, .y) |
| `CGEventGetTimestamp` | Nanoseconds since boot (mach_absolute_time) |
| `CGEventGetFlags` | Modifier flags (shift, cmd, ctrl, opt) |
| `CGEventGetIntegerValueField` | Extract key codes, click counts, scroll deltas |
| `CGEventKeyboardGetUnicodeString` | Key code → character string |
| `CGEventMaskBit` | Build event type bitmask |
| `CFMachPortCreateRunLoopSource` | Wrap tap as run loop source |
| `CFRunLoopGetCurrent` / `CFRunLoopAddSource` / `CFRunLoopRun` / `CFRunLoopStop` | Run loop lifecycle |

### Constants
| Constant | Value | Purpose |
|---|---|---|
| `kCGSessionEventTap` | 1 | Tap at session level (current user) |
| `kCGHeadInsertEventTap` | 0 | Insert at head of tap chain |
| `kCGEventTapOptionListenOnly` | 1 | **Passive — cannot modify events** |
| `kCGEventLeftMouseDown` | 1 | Left click |
| `kCGEventRightMouseDown` | 3 | Right click |
| `kCGEventKeyDown` | 10 | Key press |
| `kCGEventScrollWheel` | 22 | Scroll |
| `kCGEventTapDisabledByTimeout` | 4294967294 | Tap auto-disabled, must re-enable |
| `kCGKeyboardEventKeycode` | 9 | Field ID for raw key code |
| `kCGMouseEventClickState` | 1 | Field ID for click count |
| `kCGScrollWheelEventDeltaAxis1` | 11 | Field ID for vertical scroll delta |
| `kCFRunLoopCommonModes` | — | Run loop mode for common sources |

### Modifier flags
| Flag | Value | Key |
|---|---|---|
| `kCGEventFlagMaskShift` | 131072 | Shift |
| `kCGEventFlagMaskControl` | 262144 | Control |
| `kCGEventFlagMaskAlternate` | 524288 | Option/Alt |
| `kCGEventFlagMaskCommand` | 1048576 | Command |

---

## 2. CGEventTapCreate — Call Pattern

### C signature
```c
CFMachPortRef CGEventTapCreate(
    CGEventTapLocation tap,       // kCGSessionEventTap
    CGEventTapPlacement place,    // kCGHeadInsertEventTap
    CGEventTapOptions options,    // kCGEventTapOptionListenOnly
    CGEventMask eventsOfInterest, // bitmask of event types
    CGEventTapCallBack callback,  // Python callable
    void *userInfo                // passed to callback as refcon
);
```

### pyobjc call
```python
import Quartz

mask = (
    Quartz.CGEventMaskBit(Quartz.kCGEventLeftMouseDown) |
    Quartz.CGEventMaskBit(Quartz.kCGEventRightMouseDown) |
    Quartz.CGEventMaskBit(Quartz.kCGEventKeyDown) |
    Quartz.CGEventMaskBit(Quartz.kCGEventScrollWheel)
)

tap = Quartz.CGEventTapCreate(
    Quartz.kCGSessionEventTap,
    Quartz.kCGHeadInsertEventTap,
    Quartz.kCGEventTapOptionListenOnly,
    mask,
    callback_fn,
    None,  # userInfo
)
# Returns None if Accessibility permission not granted
```

### Callback signature (pyobjc)
```python
def callback(proxy, event_type, event, refcon):
    # proxy: opaque tap proxy
    # event_type: int (kCGEventLeftMouseDown, kCGEventKeyDown, etc.)
    # event: CGEventRef
    # refcon: userInfo passed to CGEventTapCreate

    # MUST return the event for listen-only taps (return value is ignored
    # by the system, but pyobjc bridge expects it)
    return event
```

### Verified behavior
- Returns `None` when Accessibility permission not granted (tested)
- `kCGEventTapOptionListenOnly` = truly passive, cannot modify/inject events
- Callback return value ignored for listen-only taps, but return `event` for safety

---

## 3. Key Code → Character Mapping — Verified

### CGEventKeyboardGetUnicodeString (pyobjc)
```python
max_len = 4
actual_len, chars = Quartz.CGEventKeyboardGetUnicodeString(event, max_len, None, None)
# actual_len: int — number of characters produced
# chars: str — the character(s)
```

### Test results with synthetic events
| Key code | Expected | Result |
|---|---|---|
| 0 (a) | 'a' | `actual_len=1, chars='a'` |
| 13 (w) | 'w' | `actual_len=1, chars='w'` |
| 36 (Return) | '\r' | `actual_len=1, chars='\r'` |
| 49 (Space) | ' ' | `actual_len=1, chars=' '` |
| 0 + Shift | 'A' | `actual_len=1, chars='a'` (synthetic — real events carry correct flags) |

**Note**: Synthetic events don't fully respect modifier flags for character mapping. Real events from the OS do — `CGEventKeyboardGetUnicodeString` uses the active keyboard layout and modifier state to produce the correct character. This is the right API to use.

### Special key handling
For non-printable keys (Return, Tab, Escape, arrows, function keys), `CGEventKeyboardGetUnicodeString` returns control characters or empty strings. Strategy:
- If `actual_len == 0` or `chars` is a control char: fall back to the raw key code
- Map common non-printable key codes to names: `{36: "Return", 48: "Tab", 53: "Escape", 51: "Delete", 123: "Left", 124: "Right", 125: "Down", 126: "Up"}`
- For the VLM prompt, these become `[Return]`, `[Tab]`, etc.

---

## 4. Threading Model — CFRunLoop in Daemon Thread

CGEventTap requires a CFRunLoop to dispatch events. The capture.py screenshot loop uses `time.sleep()` in the main thread. These are independent — the event tap runs in a daemon thread with its own CFRunLoop.

### Pattern (verified from Hammerspoon, pqrs-org examples, and PyObjC docs)
```python
import threading
import Quartz

loop_ref = None

def _event_tap_thread(tap, source):
    global loop_ref
    loop_ref = Quartz.CFRunLoopGetCurrent()
    Quartz.CFRunLoopAddSource(loop_ref, source, Quartz.kCFRunLoopCommonModes)
    Quartz.CGEventTapEnable(tap, True)
    Quartz.CFRunLoopRun()  # blocks until CFRunLoopStop called

# Start:
source = Quartz.CFMachPortCreateRunLoopSource(None, tap, 0)
thread = threading.Thread(target=_event_tap_thread, args=(tap, source), daemon=True)
thread.start()

# Stop:
Quartz.CFRunLoopStop(loop_ref)
thread.join(timeout=2)
```

### Key design points
- **Daemon thread**: dies automatically if main thread exits (no orphan risk)
- **CFRunLoopRun() blocks**: the thread sits in the run loop until `CFRunLoopStop()` is called
- **Thread safety for events list**: callback appends to a list; Python's GIL makes `list.append()` thread-safe for simple appends. No lock needed.
- **Cleanup order**: Stop run loop → join thread → write events.json

---

## 5. kCGEventTapDisabledByTimeout — Must Handle

If the callback takes too long (>~1 second), macOS auto-disables the tap and sends a `kCGEventTapDisabledByTimeout` event. This is the #1 gotcha.

### Fix (verified from Hammerspoon, pqrs-org, iTerm2)
```python
def callback(proxy, event_type, event, refcon):
    if event_type == Quartz.kCGEventTapDisabledByTimeout:
        Quartz.CGEventTapEnable(tap, True)  # re-enable
        return event
    # ... normal event handling
```

### Risk assessment
Our callback is trivial (~5 lines: extract fields, append to list). It will never take >1ms. Timeout is extremely unlikely, but the re-enable handler costs nothing and prevents silent event loss.

---

## 6. Permission Requirements

### Same as existing harness — Accessibility permission
- CGEventTap requires the process to be "trusted for accessibility"
- The harness already checks this in `MacOSDesktopEnvironment._check_accessibility_permission()`
- `capture.py` can reuse the same check
- If `CGEventTapCreate` returns `None`, events should be skipped gracefully (screenshots continue)

### No additional permissions needed
- CGEventTap (listen-only) does not require Input Monitoring permission separately on macOS 13+
- It only requires Accessibility, which the user already grants for the harness

---

## 7. Timestamp Correlation Strategy

### Problem
- `CGEventGetTimestamp()` returns mach_absolute_time (nanoseconds since boot)
- `capture.py` frame timestamps use `int(time.time())` (Unix epoch seconds)
- These are different time bases

### Solution
Record `start_epoch = time.time()` when the event tap starts, and store the first event's mach timestamp as `base_mach_ts`. Then:
```
event_epoch = start_epoch + (event_mach_ts - base_mach_ts) / 1e9
```

Or simpler: just store **relative seconds from capture start** in events.json. The capture session already records `captured_at` in the manifest. Events become: `"t": 1.234` meaning "1.234 seconds after capture started."

**Recommended**: Use `time.monotonic()` at capture start and in the callback. Avoid mach timestamp conversion entirely:
```python
capture_start = time.monotonic()
# In callback:
relative_t = round(time.monotonic() - capture_start, 3)
```
This is simpler, avoids mach_absolute_time conversion pitfalls, and `time.monotonic()` is monotonic (no NTP jumps).

---

## 8. CGEventTap vs NSEvent.addGlobalMonitor — Decision

### Option A: CGEventTap (recommended)
- Requires only CFRunLoop — works in a daemon thread
- Direct access to raw event data (coordinates, key codes, timestamps)
- Same permission requirement (Accessibility)
- Used by Hammerspoon, Karabiner, iTerm2 — battle-tested
- pyobjc bindings verified working

### Option B: NSEvent.addGlobalMonitorForEventsMatchingMask
- Higher-level API, gives NSEvent objects with `.characters`, `.keyCode`, `.locationInWindow`
- **Requires NSApplication event loop** (AppHelper.runEventLoop() or NSApplication.run())
- Running a full NSApplication in a capture thread is heavy and fragile
- No advantage for our use case

### Decision: **CGEventTap**. It's lighter, works in a background thread with just CFRunLoop, and all the data extraction APIs are verified.

---

## 9. Privacy / Security Considerations

### This is a keylogger
CGEventTap with `kCGEventKeyDown` captures every keystroke system-wide. The events.json file will contain:
- Every character typed (including passwords, tokens, private messages)
- Every URL navigated to (via typed addresses)
- Mouse click coordinates (can reveal UI interaction patterns)

### Mitigations to document/implement
1. **User awareness**: The `--events` flag should print a clear warning: "Recording keyboard input. Evidence may contain passwords."
2. **Default-on with warning**: Events are too valuable to be opt-in, but the warning is non-negotiable
3. **Evidence directory hygiene**: Document that evidence directories should be treated as sensitive data
4. **No network transmission**: Events stay local in events.json — never uploaded without explicit user action
5. **Graceful degradation**: If Accessibility permission is denied, skip events silently (screenshots still captured)

---

## 10. Keystroke Grouping Strategy

### Problem
Raw events are individual key presses: `j`, `a`, `n`, `e`, `@`, `e`, `x`, ...
The VLM prompt needs: "typed 'jane@example.com'"

### Grouping algorithm
1. Sequential `kCGEventKeyDown` events within a short time window (~500ms between each) are grouped into a typed string
2. A non-keyboard event (click, scroll) or a pause >500ms breaks the group
3. Special keys (Return, Tab, Escape) also break the group and appear as `[Return]` etc.
4. Modifier-only combinations (Cmd+C) are separate actions: "pressed Cmd+C"

### Example transformation
```
Raw events:
  t=1.0 key 'j'
  t=1.1 key 'a'
  t=1.2 key 'n'
  t=1.3 key 'e'
  t=1.5 key '@'
  ...
  t=2.1 key 'm'
  t=2.3 key [Return]
  t=3.0 click (500, 300)

Grouped for VLM:
  At t=1.0s typed "jane@...m"
  At t=2.3s pressed [Return]
  At t=3.0s clicked (500, 300)
```

This grouping happens at **prompt construction time** (in `intent_extract.py`), not at capture time. `events.json` stores raw individual events. The grouping logic is ~20-30 lines of pure Python, easily testable.

---

## 11. Proposed events.json Schema

```json
{
  "capture_start_epoch": 1775346544.189,
  "events": [
    {
      "t": 0.000,
      "type": "mouse",
      "button": "left",
      "x": 500,
      "y": 300,
      "click_count": 1
    },
    {
      "t": 1.234,
      "type": "key",
      "char": "j",
      "keycode": 38,
      "modifiers": []
    },
    {
      "t": 1.300,
      "type": "key",
      "char": null,
      "keycode": 36,
      "key_name": "Return",
      "modifiers": ["shift"]
    },
    {
      "t": 5.678,
      "type": "key",
      "char": "c",
      "keycode": 8,
      "modifiers": ["command"]
    },
    {
      "t": 8.901,
      "type": "scroll",
      "delta_y": -3,
      "x": 640,
      "y": 400
    }
  ]
}
```

### Design decisions
- **`t`**: relative seconds from capture start (float, 3 decimal places)
- **`type`**: one of `"mouse"`, `"key"`, `"scroll"`
- **`char`**: the actual character for printable keys, `null` for special keys
- **`key_name`**: human-readable name for special keys only
- **`modifiers`**: list of active modifier names (empty list = no modifiers)
- **`button`**: `"left"` or `"right"` for mouse events
- **`click_count`**: from `kCGMouseEventClickState` (1 = single, 2 = double)
- **`x`, `y`**: integer pixel coordinates (mouse and scroll events)
- **`delta_y`**: scroll direction/amount

---

## 12. Test Strategy

### What's testable without Accessibility permission
1. **Event grouping logic** (pure Python, no OS dependency)
   - Group sequential keys into typed strings
   - Break on pause, click, special key
   - Handle modifiers (Cmd+C stays separate)
2. **Prompt construction** (pure Python)
   - Given events.json, produce human-readable timeline for VLM
3. **events.json serialization** (pure Python)
   - Event dataclass → JSON roundtrip
4. **Timestamp correlation** (pure Python)
   - `time.monotonic()` offset math

### What requires Accessibility permission (manual/integration test)
5. **CGEventTapCreate succeeds** — returns non-None
6. **Events actually captured** — click somewhere, verify event in list
7. **Thread lifecycle** — start tap thread, stop cleanly, no hangs

### Mock strategy for unit tests
- Mock `Quartz.CGEventTapCreate` to return a sentinel (skip actual tap creation)
- The callback function can be tested directly by constructing synthetic events:
  ```python
  event = Quartz.CGEventCreateKeyboardEvent(None, 0, True)  # synthetic 'a'
  ```
  This works without Accessibility permission (verified).

---

## 13. Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Accessibility permission not granted | Low (already required) | Events silently skipped | Graceful fallback, log warning |
| Tap disabled by timeout | Very low (callback is trivial) | Lose events until re-enabled | Handle `kCGEventTapDisabledByTimeout` |
| Thread doesn't stop cleanly | Low | Capture hangs on Ctrl+C | Daemon thread + CFRunLoopStop + join(timeout=2) |
| Memory leak from CGEventTapCreate | None (called once) | — | Apple docs note leak on repeated calls; we call once |
| Keystroke capture records passwords | Certain | Privacy risk | Warning message, documentation, local-only storage |
| pyobjc callback crash | Low | Capture dies | Try/except in callback, log error, continue |
| GIL contention | Very low | Slight timing jitter | Callback is <1ms, append is atomic |

**Overall risk: Low.** The API is stable (since macOS 10.4), well-documented, and used by major tools (Hammerspoon, Karabiner, iTerm2). The pyobjc bindings are verified working. The implementation is ~80-100 lines with no new dependencies.

---

## Sources

- [PyObjC Quartz API Notes](https://pyobjc.readthedocs.io/en/latest/apinotes/Quartz.html)
- [CGEventTapCallBack — Apple Developer Documentation](https://developer.apple.com/documentation/coregraphics/cgeventtapcallback)
- [CGEventTapCreate — Apple SDK Headers](https://github.com/phracker/MacOSX-SDKs/blob/master/MacOSX10.9.sdk/System/Library/Frameworks/CoreGraphics.framework/Versions/A/Headers/CGEvent.h)
- [pqrs-org CGEventTap Example](https://github.com/pqrs-org/osx-event-observer-examples/blob/main/cgeventtap-example/src/CGEventTapExample.m)
- [Hammerspoon eventtap implementation](https://github.com/Hammerspoon/hammerspoon/blob/master/extensions/eventtap/libeventtap.m)
- [PyObjC keypress listener gist (ljos)](https://gist.github.com/ljos/3019549)
- [KeePassXC CGEventTap discussion](https://github.com/keepassxreboot/keepassxc/issues/3393)
- [CGEventTap tap disabled bug report](https://github.com/feedback-assistant/reports/issues/390)
- [tapDisabledByTimeout — Apple Developer Documentation](https://developer.apple.com/documentation/coregraphics/cgeventtype/tapdisabledbytimeout)
