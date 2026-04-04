# Playwright Python API Notes

Source: playwright.dev/python (verified April 2026)

---

## 1. Import Path (Async API)

```python
from playwright.async_api import async_playwright
```

The sync API uses `from playwright.sync_api import sync_playwright`. All async usage requires the `async_api` module.

Source: https://playwright.dev/python/docs/library

---

## 2. Launch Browser and Create Context with Fixed Viewport and Downloads Path

`downloads_path` is a **launch-level** option on `browser_type.launch()`, not on `new_context()`.
`viewport` is a **context-level** option on `browser.new_context()`.
`accept_downloads` (bool, defaults to `true`) is also context-level.

```python
async with async_playwright() as p:
    browser = await p.chromium.launch(
        downloads_path="/path/to/downloads"  # Union[str, pathlib.Path], optional
        # If omitted, a temp dir is created and deleted when the browser closes
    )

    context = await browser.new_context(
        viewport={"width": 1280, "height": 720},  # Dict with width/height int keys
        accept_downloads=True,                     # bool, defaults to True
    )

    page = await context.new_page()
```

Key notes:
- `downloads_path` type: `Union[str, pathlib.Path]`
- `viewport` type: `NoneType | Dict` — keys `width` (int) and `height` (int)
- Default viewport: 1280x720; pass `no_viewport=True` on context to disable fixed viewport
- Downloaded files are deleted when the browser context that produced them is closed
  (even if `downloads_path` is set — save explicitly with `download.save_as()`)

Sources:
- https://playwright.dev/python/docs/api/class-browsertype#browser-type-launch
- https://playwright.dev/python/docs/api/class-browser#browser-new-context
- https://playwright.dev/python/docs/downloads

---

## 3. Handling Downloads with page.expect_download()

```python
# Async version (use `await` on the value)
async with page.expect_download() as download_info:
    await page.click("a[download]")   # action that triggers the download

download = await download_info.value

# Access metadata
print(download.suggested_filename)   # str — server-suggested filename
path = await download.path()         # pathlib.Path — temp file location

# Save to a permanent location
await download.save_as("/destination/" + download.suggested_filename)
```

Signature:
```python
page.expect_download(predicate=None, timeout=None)
# -> EventContextManager[Download]
```

Parameters:
- `predicate` — `Callable[[Download], bool]` (optional): filter; resolves when truthy
- `timeout` — `float` (optional): ms, defaults to 30000; pass `0` to disable

Notes:
- Returns an `EventContextManager[Download]`; the `Download` object is accessed via `.value`
- Throws if the page closes before the download event fires
- Alternative event-based pattern: `page.on("download", handler)` — but this forks control
  flow and the main coroutine does not await download completion; avoid for deterministic scripts

Sources:
- https://playwright.dev/python/docs/api/class-page#page-expect-download
- https://playwright.dev/python/docs/downloads
- https://playwright.dev/python/docs/api/class-download

---

## 4. ariaSnapshot() — Method Name and Usage

In Playwright Python the method is **`aria_snapshot()`** (snake_case, not camelCase).

Available on both `Page` and `Locator`:

```python
# Page-level (captures full page body)
snapshot: str = await page.aria_snapshot()

# Locator-level (captures a specific element)
snapshot: str = await page.locator("main").aria_snapshot()
snapshot: str = await page.get_by_role("navigation").aria_snapshot()
```

Signature (same for both Page and Locator):
```python
aria_snapshot(*, depth=None, mode=None, timeout=None) -> str
```

Parameters:
- `depth` — `int` (optional): limit recursion depth of the snapshot
- `mode` — `"ai" | "default"` (optional, default `"default"`):
  - `"default"`: standard YAML snapshot of ARIA roles/names/text
  - `"ai"`: adds element references like `[ref=e2]` and includes nested `<iframe>` snapshots;
    optimized for AI/LLM consumption
- `timeout` — `float` (optional): ms, defaults to 30000; pass `0` to disable

Return type: `str` — YAML-formatted snapshot representing roles, accessible names, text, children.

Added in: Playwright v1.59

Sources:
- https://playwright.dev/python/docs/api/class-page#page-aria-snapshot
- https://playwright.dev/python/docs/api/class-locator#locator-aria-snapshot

---

## Summary Table

| Topic | Key Detail |
|---|---|
| Async import | `from playwright.async_api import async_playwright` |
| `downloads_path` | Launch option on `browser_type.launch()`, type `Union[str, pathlib.Path]` |
| `viewport` | Context option on `browser.new_context()`, dict `{"width": int, "height": int}` |
| `accept_downloads` | Context option, bool, defaults to `True` |
| Download handle | `async with page.expect_download() as dl_info:` then `await dl_info.value` |
| Save download | `await download.save_as(path)` |
| ariaSnapshot method name | `aria_snapshot()` (snake_case) on `Page` and `Locator` |
| ariaSnapshot AI mode | Pass `mode="ai"` for ref-annotated output suitable for LLM consumption |
| ariaSnapshot added | Playwright v1.59 |
