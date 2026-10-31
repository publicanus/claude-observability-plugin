"""Forwards Claude Code's API calls to api.anthropic.com, and answers HTTP 400 to a call
whose newest prompt text contains FAIL-THIS-TURN, so that turn ends in an API error.

    python3 fault_proxy.py <port>

    <port>   the local port to listen on, at 127.0.0.1
    use      run claude with ANTHROPIC_BASE_URL=http://127.0.0.1:<port>
    exit     runs until killed
"""

from __future__ import annotations

import http.client
import http.server
import json
import sys

MARKER = "FAIL-THIS-TURN"
UPSTREAM = "api.anthropic.com"
HOP_BY_HOP = {"host", "content-length", "connection", "accept-encoding", "transfer-encoding"}


def newest_prompt_has_marker(body: bytes) -> bool:
    """True when the last text of the last user message, not counting system
    reminders, holds MARKER. A prompt sent after a failed one is merged into that
    same user message, and its text then comes last."""
    try:
        messages = json.loads(body).get("messages") or []
    except ValueError:
        return False
    users = [m for m in messages if isinstance(m, dict) and m.get("role") == "user"]
    if not users:
        return False
    content = users[-1].get("content")
    blocks = content if isinstance(content, list) else [{"type": "text", "text": str(content)}]
    texts = [
        b.get("text", "")
        for b in blocks
        if isinstance(b, dict) and b.get("type") == "text" and not b.get("text", "").lstrip().startswith("<system-reminder>")
    ]
    return bool(texts) and MARKER in texts[-1]


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self) -> None:
        self.forward("POST")

    def do_GET(self) -> None:
        self.forward("GET")

    def forward(self, method: str) -> None:
        body = self.rfile.read(int(self.headers.get("content-length") or 0))
        if method == "POST" and "/messages" in self.path and newest_prompt_has_marker(body):
            error = {"type": "error", "error": {"type": "invalid_request_error", "message": "injected by fault_proxy"}}
            out = json.dumps(error).encode()
            self.send_response(400)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)
            return
        upstream = http.client.HTTPSConnection(UPSTREAM, timeout=600)
        headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP_BY_HOP}
        upstream.request(method, self.path, body=body if method == "POST" else None, headers=headers)
        response = upstream.getresponse()
        self.send_response(response.status)
        for name, value in response.getheaders():
            if name.lower() not in HOP_BY_HOP:
                self.send_header(name, value)
        self.send_header("transfer-encoding", "chunked")
        self.end_headers()
        while True:
            chunk = response.read1(65536)
            if not chunk:
                break
            self.wfile.write(b"%x\r\n%s\r\n" % (len(chunk), chunk))
            self.wfile.flush()
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()

    def log_message(self, *args: object) -> None:
        pass


def main() -> int:
    if len(sys.argv) != 2 or not sys.argv[1].isdigit():
        print("usage: fault_proxy.py <port>", file=sys.stderr)
        return 2
    http.server.ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
