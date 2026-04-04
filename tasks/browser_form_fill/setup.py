"""Setup: start a local HTTP server that serves a form and captures submissions."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

_server: HTTPServer | None = None
_thread: threading.Thread | None = None

FIXTURES_DIR = Path(__file__).parent / "fixtures"
PORT = 8766
SUBMISSION_PATH = Path("/tmp/harness_form_submission.json")


class _FormHandler(BaseHTTPRequestHandler):

    def do_GET(self) -> None:
        if self.path not in ("/", "/index.html"):
            self.send_error(404)
            return
        index = FIXTURES_DIR / "index.html"
        content = index.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def do_POST(self) -> None:
        if self.path != "/submit":
            self.send_error(404)
            return

        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8")
        fields = parse_qs(body)
        # parse_qs returns lists; flatten to single values
        submission = {k: v[0] for k, v in fields.items()}

        SUBMISSION_PATH.write_text(json.dumps(submission, indent=2))

        response_html = b"<html><body><h1>Thank you!</h1><p>Form submitted.</p></body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(response_html)))
        self.end_headers()
        self.wfile.write(response_html)

    def log_message(self, format: str, *args: Any) -> None:
        pass  # Suppress request logging


class _ReusableHTTPServer(HTTPServer):
    allow_reuse_address = True


def setup() -> dict[str, Any]:
    """Start the form server and clean any prior submission."""
    global _server, _thread

    # Clean prior submission
    if SUBMISSION_PATH.exists():
        SUBMISSION_PATH.unlink()

    _server = _ReusableHTTPServer(("127.0.0.1", PORT), _FormHandler)
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
