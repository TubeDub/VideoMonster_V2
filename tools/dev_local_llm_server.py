"""Minimal local OpenAI-compatible LLM server for integration verification.

This is NOT a model — it is a tiny stdlib HTTP server that speaks the OpenAI
`/v1/models` + `/v1/chat/completions` shape so the auto-discovery + Timing-Rewrite
wiring can be exercised end-to-end without downloading a multi-GB model. It
performs a real, meaning-preserving text transformation (filler removal for
"shorten" prompts, a natural connective for "lengthen" prompts) so the pipeline
sees genuine adapted text over HTTP.

When the real Ollama model is installed it is detected automatically on
127.0.0.1:11434 with HIGHER priority than this server — no code change needed.

Run:  python tools/dev_local_llm_server.py [port]
"""

from __future__ import annotations

import json
import re
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

_FILLERS = re.compile(
    r"\b(дуже|справді|насправді|власне|загалом|просто|начебто|якось|типу|ну,?|"
    r"досить|доволі|трохи|надзвичайно|вельми)\b",
    re.IGNORECASE,
)


def _extract_line(prompt: str) -> str:
    for marker in ("Translated line:", "Line to lengthen:", "Line:"):
        if marker in prompt:
            return prompt.split(marker, 1)[1].strip()
    return prompt.strip().splitlines()[-1].strip()


def _rephrase(prompt: str) -> str:
    line = _extract_line(prompt)
    lengthen = "LONGER" in prompt or "lengthen" in prompt.lower()
    if lengthen:
        # Natural fuller phrasing — never filler/padding, keep it one sentence.
        base = line.rstrip(".!?…")
        return f"{base}, як це й було спочатку."
    # Shorten: drop fillers/softeners, collapse spaces, keep the full sentence.
    out = _FILLERS.sub("", line)
    out = re.sub(r"\s{2,}", " ", out).strip()
    out = re.sub(r"\s+([,.!?…])", r"\1", out)
    return out or line


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # silence
        pass

    def _send(self, code, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.rstrip("/").endswith("/v1/models"):
            self._send(200, {"object": "list", "data": [{"id": "vm-local-verify", "object": "model"}]})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception:
            payload = {}
        messages = payload.get("messages") or []
        prompt = messages[-1].get("content", "") if messages else ""
        text = _rephrase(prompt)
        self._send(
            200,
            {
                "id": "chatcmpl-vmlocal",
                "object": "chat.completion",
                "model": payload.get("model") or "vm-local-verify",
                "choices": [
                    {"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}
                ],
            },
        )


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    srv = HTTPServer(("127.0.0.1", port), Handler)
    print(f"[vm-local-llm] OpenAI-compatible server on http://127.0.0.1:{port}/v1", flush=True)
    srv.serve_forever()
