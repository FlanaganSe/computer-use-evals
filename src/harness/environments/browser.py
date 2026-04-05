"""Playwright browser environment for the eval harness."""

from __future__ import annotations

from pathlib import Path

from playwright.async_api import (
    Browser,
    BrowserContext,
    Download,
    Page,
    Playwright,
    async_playwright,
)

from harness.runtime_results import ExecutionMethod, RuntimeResult, done, error, fail, ok
from harness.types import Action, ActionType, Observation, ObservationType, Task

VIEWPORT_WIDTH = 1280
VIEWPORT_HEIGHT = 720


class BrowserEnvironment:
    """Manages a Playwright browser lifecycle for task execution."""

    def __init__(self) -> None:
        self._playwright: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._run_dir: Path | None = None
        self._downloads_path: Path | None = None

    @property
    def page(self) -> Page:
        if self._page is None:
            msg = "Browser not set up — call setup() first"
            raise RuntimeError(msg)
        return self._page

    async def setup(self, task: Task, run_dir: Path) -> None:
        self._run_dir = run_dir
        self._downloads_path = run_dir / "artifacts"
        self._downloads_path.mkdir(parents=True, exist_ok=True)
        (run_dir / "screenshots").mkdir(parents=True, exist_ok=True)

        import os

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=os.environ.get("HARNESS_HEADLESS", "1") != "0",
            downloads_path=str(self._downloads_path),
        )
        self._context = await self._browser.new_context(
            viewport={"width": VIEWPORT_WIDTH, "height": VIEWPORT_HEIGHT},
            accept_downloads=True,
        )
        self._page = await self._context.new_page()
        self._page.on("download", self._handle_download)

    def _handle_download(self, download: Download) -> None:
        """Auto-save any download with its suggested filename."""
        import asyncio

        async def _save(dl: Download) -> None:
            if self._downloads_path is not None:
                await dl.save_as(str(self._downloads_path / dl.suggested_filename))

        asyncio.ensure_future(_save(download))

    async def collect_observation(self, observation_type: ObservationType) -> Observation:
        if observation_type == ObservationType.NONE:
            return Observation(observation_type=ObservationType.NONE)

        page = self.page
        url = page.url
        title = await page.title()

        screenshot: bytes | None = None
        aria_snapshot: str | None = None

        if observation_type in (ObservationType.SCREENSHOT, ObservationType.SCREENSHOT_AND_ARIA):
            screenshot = await page.screenshot(type="png")

        if observation_type in (ObservationType.ARIA_STATE, ObservationType.SCREENSHOT_AND_ARIA):
            aria_snapshot = await page.locator("body").aria_snapshot()

        return Observation(
            observation_type=observation_type,
            screenshot=screenshot,
            aria_snapshot=aria_snapshot,
            url=url,
            page_title=title,
        )

    async def execute_action(self, action: Action) -> RuntimeResult:
        page = self.page
        params = action.params

        match action.action_type:
            case ActionType.GOTO:
                await page.goto(params["url"], wait_until="domcontentloaded")
                return ok(method=ExecutionMethod.OTHER)

            case ActionType.CLICK:
                expect_download = params.get("expect_download", False)
                if "selector" in params and expect_download:
                    async with page.expect_download() as dl_info:
                        await page.click(params["selector"])
                    download = await dl_info.value
                    if self._downloads_path is not None:
                        save_as = params.get("save_as", download.suggested_filename)
                        save_path = self._downloads_path / save_as
                        await download.save_as(str(save_path))
                        return ok(
                            f"downloaded:{save_path.name}",
                            method=ExecutionMethod.SELECTOR,
                            metadata={"filename": save_path.name},
                        )
                    return ok(method=ExecutionMethod.SELECTOR)
                elif "selector" in params:
                    await page.click(params["selector"])
                    return ok(method=ExecutionMethod.SELECTOR)
                elif "x" in params and "y" in params:
                    await page.mouse.click(params["x"], params["y"])
                    return ok(method=ExecutionMethod.COORDINATES)
                else:
                    return error("click requires selector or coordinates", target_resolved=False)

            case ActionType.TYPE:
                if "selector" in params:
                    await page.fill(params["selector"], params["text"])
                else:
                    await page.keyboard.type(params["text"])
                return ok(method=ExecutionMethod.KEYBOARD)

            case ActionType.PRESS:
                await page.keyboard.press(params["key"])
                return ok(method=ExecutionMethod.KEYBOARD)

            case ActionType.SCROLL:
                await page.mouse.wheel(
                    params.get("delta_x", 0),
                    params.get("delta_y", 0),
                )
                return ok(method=ExecutionMethod.COORDINATES)

            case ActionType.WAIT:
                ms = params.get("ms", 1000)
                await page.wait_for_timeout(ms)
                return ok(method=ExecutionMethod.WAIT)

            case ActionType.DONE:
                return done()

            case ActionType.FAIL:
                reason = params.get("reason", "Agent declared failure")
                return fail(reason)

            case _:
                return error(f"unsupported action type {action.action_type}")

    async def teardown(self) -> None:
        if self._context is not None:
            await self._context.close()
            self._context = None
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None
        self._page = None
