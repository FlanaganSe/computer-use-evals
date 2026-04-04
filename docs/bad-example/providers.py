"""
Perception providers: how the system sees the screen.

This is where the GPA research insight gets operationalized. The key finding
is that representing the screen as a GRAPH of elements (with visual embeddings
and spatial relationships) is fundamentally more efficient than raw screenshots
for deterministic replay. But accessibility trees, where available, provide
even richer structured data at ~12x lower token cost.

The eval framework supports all perception modes so you can measure the
tradeoff between perception quality, cost, and availability across different
app types.

Cross-platform design:
- Browser: Playwright CDP gives us both DOM and screenshots
- macOS: NSAccessibility API via pyobjc (or osascript as fallback)
- Windows: UI Automation via comtypes/uiautomation
- Linux: AT-SPI via pyatspi2
"""

from __future__ import annotations

import abc
import base64
import io
import json
import logging
import time
from pathlib import Path
from typing import Any, Optional

from .types import (
    BoundingBox,
    PerceptionMode,
    ScreenState,
    UIElement,
    UIGraph,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------

class PerceptionProvider(abc.ABC):
    """Base class for all perception providers.

    Each provider implements `capture()` which returns a ScreenState
    populated with whatever data that perception mode can provide.
    """

    @property
    @abc.abstractmethod
    def mode(self) -> PerceptionMode:
        ...

    @abc.abstractmethod
    async def capture(self, **kwargs: Any) -> ScreenState:
        """Capture current screen state."""
        ...

    @abc.abstractmethod
    async def is_available(self) -> bool:
        """Check if this perception mode is available in current environment."""
        ...


# ---------------------------------------------------------------------------
# Screenshot perception (baseline / universal fallback)
# ---------------------------------------------------------------------------

class ScreenshotPerception(PerceptionProvider):
    """Pure screenshot-based perception.

    This is the universal fallback — works everywhere, but costs ~50K tokens
    per frame and loses structural information. Used as the control in evals.
    """

    def __init__(self, playwright_page=None, resolution: tuple[int, int] = (1920, 1080)):
        self._page = playwright_page
        self._resolution = resolution

    @property
    def mode(self) -> PerceptionMode:
        return PerceptionMode.SCREENSHOT

    async def is_available(self) -> bool:
        return True  # Screenshots always available

    async def capture(self, **kwargs: Any) -> ScreenState:
        start = time.monotonic()
        state = ScreenState(
            screen_resolution=self._resolution,
            metadata={"perception_mode": "screenshot"},
        )

        if self._page is not None:
            # Browser context: use Playwright
            screenshot_bytes = await self._page.screenshot(full_page=False)
            state.screenshot_base64 = base64.b64encode(screenshot_bytes).decode()
            state.window_title = await self._page.title()
            state.active_app = "browser"
        else:
            # Desktop context: use platform-specific screenshot
            state.screenshot_base64 = await self._capture_desktop_screenshot()

        state.metadata["capture_latency_ms"] = (time.monotonic() - start) * 1000
        return state

    async def _capture_desktop_screenshot(self) -> Optional[str]:
        """Cross-platform desktop screenshot. Returns base64-encoded PNG."""
        import subprocess
        import platform
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            tmp_path = f.name

        system = platform.system()
        try:
            if system == "Darwin":
                subprocess.run(["screencapture", "-x", tmp_path], check=True)
            elif system == "Linux":
                # Try multiple tools in order of preference
                for cmd in [
                    ["gnome-screenshot", "-f", tmp_path],
                    ["scrot", tmp_path],
                    ["import", "-window", "root", tmp_path],  # ImageMagick
                ]:
                    try:
                        subprocess.run(cmd, check=True, timeout=5)
                        break
                    except (subprocess.CalledProcessError, FileNotFoundError):
                        continue
            elif system == "Windows":
                # PowerShell screenshot
                ps_script = f"""
                Add-Type -AssemblyName System.Windows.Forms
                $screen = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
                $bitmap = New-Object System.Drawing.Bitmap($screen.Width, $screen.Height)
                $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
                $graphics.CopyFromScreen($screen.Location, [System.Drawing.Point]::Empty, $screen.Size)
                $bitmap.Save('{tmp_path}')
                """
                subprocess.run(["powershell", "-Command", ps_script], check=True)

            with open(tmp_path, "rb") as f:
                return base64.b64encode(f.read()).decode()
        except Exception as e:
            logger.warning(f"Desktop screenshot failed: {e}")
            return None
        finally:
            Path(tmp_path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Accessibility tree perception
# ---------------------------------------------------------------------------

class AccessibilityTreePerception(PerceptionProvider):
    """Structured perception via OS accessibility APIs.

    ~12x cheaper than screenshots in token cost. Provides semantic roles,
    labels, states, and hierarchy. But availability varies wildly:
    Screen2AX found only 33% of macOS apps have full a11y support.
    """

    def __init__(self, playwright_page=None):
        self._page = playwright_page

    @property
    def mode(self) -> PerceptionMode:
        return PerceptionMode.ACCESSIBILITY_TREE

    async def is_available(self) -> bool:
        if self._page is not None:
            return True  # Browser a11y tree always available via CDP
        # Desktop: check platform-specific availability
        return await self._check_desktop_a11y()

    async def capture(self, **kwargs: Any) -> ScreenState:
        start = time.monotonic()
        state = ScreenState(metadata={"perception_mode": "accessibility_tree"})

        if self._page is not None:
            state.accessibility_tree = await self._capture_browser_a11y()
            state.window_title = await self._page.title()
            state.active_app = "browser"
        else:
            state.accessibility_tree = await self._capture_desktop_a11y()

        state.metadata["capture_latency_ms"] = (time.monotonic() - start) * 1000
        state.metadata["tree_node_count"] = self._count_nodes(state.accessibility_tree)
        return state

    async def _capture_browser_a11y(self) -> Optional[dict]:
        """Extract accessibility tree from browser via Playwright."""
        if self._page is None:
            return None
        try:
            # Playwright's accessibility snapshot
            tree = await self._page.accessibility.snapshot()
            return tree
        except Exception as e:
            logger.warning(f"Browser a11y capture failed: {e}")
            return None

    async def _capture_desktop_a11y(self) -> Optional[dict]:
        """Extract accessibility tree from desktop. Cross-platform."""
        import platform
        system = platform.system()

        try:
            if system == "Darwin":
                return await self._macos_a11y()
            elif system == "Windows":
                return await self._windows_a11y()
            elif system == "Linux":
                return await self._linux_a11y()
        except Exception as e:
            logger.warning(f"Desktop a11y capture failed ({system}): {e}")
            return None

    async def _macos_a11y(self) -> Optional[dict]:
        """macOS accessibility via osascript/JXA."""
        import subprocess
        # JXA script to get focused app's UI element tree
        script = """
        const app = Application.currentApplication();
        app.includeStandardAdditions = true;
        const sysEvents = Application('System Events');
        const frontApp = sysEvents.processes.whose({frontmost: true})[0];
        
        function getTree(element, depth) {
            if (depth > 6) return null;
            const result = {
                role: element.role ? element.role() : 'unknown',
                name: '',
                value: '',
                position: null,
                size: null,
                children: []
            };
            try { result.name = element.name() || ''; } catch(e) {}
            try { result.value = element.value() || ''; } catch(e) {}
            try { result.position = element.position(); } catch(e) {}
            try { result.size = element.size(); } catch(e) {}
            try {
                const children = element.uiElements();
                for (let i = 0; i < Math.min(children.length, 50); i++) {
                    const child = getTree(children[i], depth + 1);
                    if (child) result.children.push(child);
                }
            } catch(e) {}
            return result;
        }
        
        JSON.stringify(getTree(frontApp, 0));
        """
        proc = subprocess.run(
            ["osascript", "-l", "JavaScript", "-e", script],
            capture_output=True, text=True, timeout=10
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return json.loads(proc.stdout.strip())
        return None

    async def _windows_a11y(self) -> Optional[dict]:
        """Windows UI Automation via PowerShell."""
        import subprocess
        script = """
        Add-Type -AssemblyName UIAutomationClient
        $root = [System.Windows.Automation.AutomationElement]::FocusedElement
        function Get-UITree($element, $depth) {
            if ($depth -gt 6) { return $null }
            $result = @{
                role = $element.Current.ControlType.ProgrammaticName
                name = $element.Current.Name
                automationId = $element.Current.AutomationId
                children = @()
            }
            try {
                $rect = $element.Current.BoundingRectangle
                $result.bounds = @{ x=$rect.X; y=$rect.Y; width=$rect.Width; height=$rect.Height }
            } catch {}
            $children = $element.FindAll([System.Windows.Automation.TreeScope]::Children,
                [System.Windows.Automation.Condition]::TrueCondition)
            foreach ($child in $children) {
                $childTree = Get-UITree $child ($depth + 1)
                if ($childTree) { $result.children += $childTree }
            }
            return $result
        }
        Get-UITree $root 0 | ConvertTo-Json -Depth 8
        """
        proc = subprocess.run(
            ["powershell", "-Command", script],
            capture_output=True, text=True, timeout=10
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return json.loads(proc.stdout.strip())
        return None

    async def _linux_a11y(self) -> Optional[dict]:
        """Linux AT-SPI via atspi2 bindings."""
        try:
            import subprocess
            # Use python-atspi or gdbus to query AT-SPI
            script = """
import gi
gi.require_version('Atspi', '2.0')
from gi.repository import Atspi
import json

def get_tree(obj, depth=0):
    if depth > 6 or obj is None:
        return None
    result = {
        'role': obj.get_role_name(),
        'name': obj.get_name() or '',
        'children': []
    }
    try:
        comp = obj.get_component_iface()
        if comp:
            rect = comp.get_extents(Atspi.CoordType.SCREEN)
            result['bounds'] = {'x': rect.x, 'y': rect.y, 'width': rect.width, 'height': rect.height}
    except: pass
    for i in range(min(obj.get_child_count(), 50)):
        child = get_tree(obj.get_child_at_index(i), depth + 1)
        if child:
            result['children'].append(child)
    return result

desktop = Atspi.get_desktop(0)
focused = None
for i in range(desktop.get_child_count()):
    app = desktop.get_child_at_index(i)
    # Find the focused app (simplified)
    if app and app.get_child_count() > 0:
        focused = app
        break

if focused:
    print(json.dumps(get_tree(focused)))
"""
            proc = subprocess.run(
                ["python3", "-c", script],
                capture_output=True, text=True, timeout=10
            )
            if proc.returncode == 0 and proc.stdout.strip():
                return json.loads(proc.stdout.strip())
        except Exception as e:
            logger.warning(f"Linux AT-SPI failed: {e}")
        return None

    async def _check_desktop_a11y(self) -> bool:
        import platform
        system = platform.system()
        if system == "Darwin":
            # Check if accessibility permissions granted
            import subprocess
            try:
                proc = subprocess.run(
                    ["osascript", "-e",
                     'tell application "System Events" to get name of first process'],
                    capture_output=True, timeout=5
                )
                return proc.returncode == 0
            except Exception:
                return False
        return True  # Assume available on other platforms

    def _count_nodes(self, tree: Optional[dict]) -> int:
        if tree is None:
            return 0
        count = 1
        for child in tree.get("children", []):
            count += self._count_nodes(child)
        return count


# ---------------------------------------------------------------------------
# UI Graph perception (GPA-style)
# ---------------------------------------------------------------------------

class UIGraphPerception(PerceptionProvider):
    """GPA-style UI graph construction from screenshots.

    Uses element detection (OCR + icon detection) to build a graph where
    nodes are UI elements and edges connect spatially nearby elements.
    This is the representation that enables deterministic replay via
    graph matching.

    In the POC, we use Playwright's element detection for browser and
    a simple heuristic detector for desktop. Production would use a
    finetuned detector like GPA's OmniParser fork.
    """

    def __init__(self, playwright_page=None, knn_k: int = 8):
        self._page = playwright_page
        self._knn_k = knn_k

    @property
    def mode(self) -> PerceptionMode:
        return PerceptionMode.UI_GRAPH

    async def is_available(self) -> bool:
        return True

    async def capture(self, **kwargs: Any) -> ScreenState:
        start = time.monotonic()
        state = ScreenState(metadata={"perception_mode": "ui_graph"})

        if self._page is not None:
            elements = await self._detect_browser_elements()
        else:
            elements = await self._detect_desktop_elements()

        edges = self._build_knn_edges(elements, k=self._knn_k)
        viewport = None
        if self._page is not None:
            vp = self._page.viewport_size
            if vp:
                viewport = BoundingBox(0, 0, vp["width"], vp["height"])

        state.ui_graph = UIGraph(
            elements=elements,
            edges=edges,
            window_bounds=viewport,
        )
        state.metadata["capture_latency_ms"] = (time.monotonic() - start) * 1000
        state.metadata["element_count"] = len(elements)
        state.metadata["edge_count"] = len(edges)
        return state

    async def _detect_browser_elements(self) -> list[UIElement]:
        """Detect interactive elements via Playwright CDP."""
        if self._page is None:
            return []

        # Use JavaScript to enumerate visible interactive elements
        elements_data = await self._page.evaluate("""
        () => {
            const interactive = 'a, button, input, select, textarea, [role="button"], '
                + '[role="link"], [role="tab"], [role="menuitem"], [role="checkbox"], '
                + '[role="radio"], [role="switch"], [role="textbox"], [role="combobox"], '
                + '[contenteditable="true"], label, [tabindex]';
            
            const results = [];
            const seen = new Set();
            
            document.querySelectorAll(interactive).forEach((el, idx) => {
                const rect = el.getBoundingClientRect();
                if (rect.width === 0 || rect.height === 0) return;
                if (rect.bottom < 0 || rect.top > window.innerHeight) return;
                if (rect.right < 0 || rect.left > window.innerWidth) return;
                
                const vw = window.innerWidth;
                const vh = window.innerHeight;
                
                const key = `${Math.round(rect.x)},${Math.round(rect.y)}`;
                if (seen.has(key)) return;
                seen.add(key);
                
                results.push({
                    id: `el_${idx}`,
                    role: el.getAttribute('role') || el.tagName.toLowerCase(),
                    text: (el.textContent || '').trim().slice(0, 100),
                    name: el.getAttribute('aria-label') || el.getAttribute('name') || '',
                    value: el.value || '',
                    bbox: {
                        x: rect.x / vw,
                        y: rect.y / vh,
                        width: rect.width / vw,
                        height: rect.height / vh
                    },
                    enabled: !el.disabled,
                    visible: true
                });
            });
            
            return results;
        }
        """)

        return [
            UIElement(
                element_id=d["id"],
                role=d["role"],
                text=d["text"] or None,
                name=d["name"] or None,
                value=d["value"] or None,
                bbox=BoundingBox(
                    d["bbox"]["x"], d["bbox"]["y"],
                    d["bbox"]["width"], d["bbox"]["height"]
                ),
                state={"enabled": d.get("enabled", True)},
                source=PerceptionMode.UI_GRAPH,
            )
            for d in elements_data
        ]

    async def _detect_desktop_elements(self) -> list[UIElement]:
        """Detect desktop UI elements.

        In POC: falls back to accessibility tree and converts to UIElements.
        In production: would use a finetuned element detector (OmniParser/GPA).
        """
        a11y = AccessibilityTreePerception()
        if await a11y.is_available():
            state = await a11y.capture()
            if state.accessibility_tree:
                return self._a11y_tree_to_elements(state.accessibility_tree)
        return []

    def _a11y_tree_to_elements(
        self, tree: dict, elements: Optional[list[UIElement]] = None, counter: Optional[list[int]] = None
    ) -> list[UIElement]:
        """Convert accessibility tree to flat list of UIElements."""
        if elements is None:
            elements = []
        if counter is None:
            counter = [0]

        bounds = tree.get("bounds") or tree.get("position")
        if bounds and isinstance(bounds, dict):
            bbox = BoundingBox(
                x=bounds.get("x", 0),
                y=bounds.get("y", 0),
                width=bounds.get("width", 0),
                height=bounds.get("height", 0),
            )
            elements.append(UIElement(
                element_id=f"a11y_{counter[0]}",
                role=tree.get("role", "unknown"),
                text=tree.get("name", "") or tree.get("value", ""),
                name=tree.get("name"),
                value=tree.get("value"),
                bbox=bbox,
                source=PerceptionMode.ACCESSIBILITY_TREE,
            ))
            counter[0] += 1

        for child in tree.get("children", []):
            self._a11y_tree_to_elements(child, elements, counter)

        return elements

    def _build_knn_edges(self, elements: list[UIElement], k: int = 8) -> list[tuple[str, str]]:
        """Build KNN edges based on spatial proximity (GPA-style)."""
        edges: list[tuple[str, str]] = []
        if len(elements) < 2:
            return edges

        # Simple O(n²) KNN — fine for typical UI element counts (<500)
        for i, el_a in enumerate(elements):
            distances: list[tuple[float, int]] = []
            cx_a, cy_a = el_a.bbox.center

            for j, el_b in enumerate(elements):
                if i == j:
                    continue
                cx_b, cy_b = el_b.bbox.center
                dist = ((cx_a - cx_b) ** 2 + (cy_a - cy_b) ** 2) ** 0.5
                distances.append((dist, j))

            distances.sort(key=lambda x: x[0])
            for dist, j in distances[:k]:
                edge = (el_a.element_id, elements[j].element_id)
                reverse = (elements[j].element_id, el_a.element_id)
                if reverse not in edges:
                    edges.append(edge)

        return edges


# ---------------------------------------------------------------------------
# Hybrid perception (the production approach)
# ---------------------------------------------------------------------------

class HybridPerception(PerceptionProvider):
    """Hybrid perception: a11y tree primary, screenshot + UI graph fallback.

    This is the approach the 2026 research converges on. Use structured
    data when available; fall back to vision when it's not. The eval
    measures how often each channel is needed and what it costs.
    """

    def __init__(self, playwright_page=None, knn_k: int = 8):
        self._screenshot = ScreenshotPerception(playwright_page)
        self._a11y = AccessibilityTreePerception(playwright_page)
        self._graph = UIGraphPerception(playwright_page, knn_k)

    @property
    def mode(self) -> PerceptionMode:
        return PerceptionMode.HYBRID

    async def is_available(self) -> bool:
        return True

    async def capture(self, **kwargs: Any) -> ScreenState:
        start = time.monotonic()

        # Always capture screenshot (needed for visual fallback)
        screenshot_state = await self._screenshot.capture()

        # Try accessibility tree
        a11y_available = await self._a11y.is_available()
        a11y_state = await self._a11y.capture() if a11y_available else ScreenState()

        # Build UI graph (from a11y if available, from detection otherwise)
        graph_state = await self._graph.capture()

        # Merge into single state
        merged = ScreenState(
            screenshot_base64=screenshot_state.screenshot_base64,
            screenshot_path=screenshot_state.screenshot_path,
            accessibility_tree=a11y_state.accessibility_tree,
            ui_graph=graph_state.ui_graph,
            active_app=screenshot_state.active_app or a11y_state.active_app,
            window_title=screenshot_state.window_title or a11y_state.window_title,
            screen_resolution=screenshot_state.screen_resolution,
            metadata={
                "perception_mode": "hybrid",
                "a11y_available": a11y_available,
                "a11y_node_count": a11y_state.metadata.get("tree_node_count", 0),
                "graph_element_count": graph_state.metadata.get("element_count", 0),
                "graph_edge_count": graph_state.metadata.get("edge_count", 0),
                "capture_latency_ms": (time.monotonic() - start) * 1000,
            },
        )

        return merged


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def create_perception(
    mode: PerceptionMode,
    playwright_page=None,
    **kwargs: Any,
) -> PerceptionProvider:
    """Create a perception provider for the specified mode."""
    providers = {
        PerceptionMode.SCREENSHOT: ScreenshotPerception,
        PerceptionMode.ACCESSIBILITY_TREE: AccessibilityTreePerception,
        PerceptionMode.UI_GRAPH: UIGraphPerception,
        PerceptionMode.HYBRID: HybridPerception,
    }
    cls = providers.get(mode)
    if cls is None:
        raise ValueError(f"Unknown perception mode: {mode}")
    return cls(playwright_page=playwright_page, **kwargs)
