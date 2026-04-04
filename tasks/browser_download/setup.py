"""Setup: start a local HTTP server serving the test fixtures."""

from __future__ import annotations

import threading
from functools import partial
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from typing import Any

_server: HTTPServer | None = None
_thread: threading.Thread | None = None

FIXTURES_DIR = Path(__file__).parent / "fixtures"
PORT = 8765


def setup() -> dict[str, Any]:
    """Start a local HTTP server on PORT serving FIXTURES_DIR."""
    global _server, _thread

    handler = partial(SimpleHTTPRequestHandler, directory=str(FIXTURES_DIR))
    _server = HTTPServer(("127.0.0.1", PORT), handler)
    _thread = threading.Thread(target=_server.serve_forever, daemon=True)
    _thread.start()

    return {"port": PORT, "base_url": f"http://localhost:{PORT}"}


def cleanup() -> None:
    """Stop the HTTP server."""
    global _server, _thread
    if _server is not None:
        _server.shutdown()
        _server = None
    if _thread is not None:
        _thread.join(timeout=5)
        _thread = None
