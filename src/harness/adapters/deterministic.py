"""Deterministic adapter: hardcoded Playwright actions for known tasks."""

from __future__ import annotations

from collections.abc import Callable

from harness.runtime_results import RuntimeResult
from harness.types import Action, ActionType, Observation, ObservationType, Task


class DeterministicAdapter:
    """Executes a hardcoded script for each known task.

    Does not observe the environment — returns a fixed sequence of actions.
    Proves the harness plumbing works and serves as a permanent baseline.
    """

    @property
    def name(self) -> str:
        return "deterministic"

    def observation_request(self) -> ObservationType:
        return ObservationType.NONE

    def decide(self, observation: Observation, task: Task) -> list[Action]:
        script = _TASK_SCRIPTS.get(task.task_id)
        if script is None:
            return [
                Action(
                    action_type=ActionType.FAIL,
                    params={"reason": f"No deterministic script for task: {task.task_id}"},
                )
            ]

        actions = script(task)
        if not actions:
            return [Action(action_type=ActionType.DONE)]
        return actions

    def reset(self) -> None:
        pass

    def notify_result(self, action: Action, result: RuntimeResult) -> None:
        pass


def _browser_download_script(task: Task) -> list[Action]:
    """Hardcoded actions for the browser-download task."""
    url_var = task.goal.variables.get("url")
    base_url = url_var.default if url_var else "http://localhost:8765"
    # The URL points to the server root; we click the download link on the page
    base = base_url.rsplit("/", 1)[0] if "/" in base_url.split("//", 1)[-1] else base_url

    filename_var = task.goal.variables.get("filename")
    filename = filename_var.default if filename_var else "test.pdf"

    return [
        Action(action_type=ActionType.GOTO, params={"url": base}),
        Action(
            action_type=ActionType.CLICK,
            params={
                "selector": "#download-link",
                "expect_download": True,
                "save_as": filename,
            },
        ),
        Action(action_type=ActionType.DONE),
    ]


def _browser_form_fill_script(task: Task) -> list[Action]:
    """Hardcoded actions for the browser-form-fill task."""
    url_var = task.goal.variables.get("url")
    url = url_var.default if url_var else "http://localhost:8766"

    name_var = task.goal.variables.get("name")
    name = name_var.default if name_var else "Jane Doe"

    email_var = task.goal.variables.get("email")
    email = email_var.default if email_var else "jane@example.com"

    return [
        Action(action_type=ActionType.GOTO, params={"url": url}),
        Action(action_type=ActionType.TYPE, params={"selector": "#name", "text": name}),
        Action(action_type=ActionType.TYPE, params={"selector": "#email", "text": email}),
        Action(action_type=ActionType.CLICK, params={"selector": "#submit-btn"}),
        Action(action_type=ActionType.WAIT, params={"ms": 500}),
        Action(action_type=ActionType.DONE),
    ]


def _desktop_textedit_save_script(task: Task) -> list[Action]:
    """Hardcoded actions for the desktop-textedit-save task (AppleScript via SHELL)."""
    content_var = task.goal.variables.get("content")
    content = content_var.default if content_var else "Hello from the eval harness"

    filename_var = task.goal.variables.get("filename")
    filename = filename_var.default if filename_var else "harness-test.txt"

    directory_var = task.goal.variables.get("directory")
    directory = directory_var.default if directory_var else "/tmp/harness_desktop_test"

    # Escape double quotes for AppleScript string literals
    content_escaped = content.replace('"', '\\"')
    directory_escaped = directory.replace('"', '\\"')
    filename_escaped = filename.replace('"', '\\"')

    applescript = (
        'tell application "TextEdit"\n'
        "    activate\n"
        "    make new document\n"
        f'    set text of front document to "{content_escaped}"\n'
        f'    save front document in POSIX file "{directory_escaped}/{filename_escaped}"\n'
        "end tell"
    )

    return [
        Action(
            action_type=ActionType.SHELL,
            params={"command": "osascript", "args": ["-e", applescript]},
        ),
        Action(action_type=ActionType.DONE),
    ]


_TASK_SCRIPTS: dict[str, Callable[[Task], list[Action]]] = {
    "browser-download": _browser_download_script,
    "browser-form-fill": _browser_form_fill_script,
    "desktop-textedit-save": _desktop_textedit_save_script,
}
