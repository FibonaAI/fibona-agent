#!/usr/bin/env python3
"""Serve the local dashboard: python eval/dashboard_server.py (PORT defaults to 8765)."""

import json
import os
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from dashboard_data import build_jobs

ROOT = Path(__file__).resolve().parent


class Handler(SimpleHTTPRequestHandler):
    def do_GET(self):
        if urlsplit(self.path).path == "/data.js":
            content = ("window.JOBS=" + json.dumps(build_jobs(ROOT / "jobs"), ensure_ascii=False) + ";").encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/javascript; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(content)
            return
        super().do_GET()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8765"))
    handler = partial(Handler, directory=str(ROOT / "dashboard"))
    with ThreadingHTTPServer(("127.0.0.1", port), handler) as server:
        print(f"Evaluation dashboard: http://127.0.0.1:{server.server_port}", flush=True)
        server.serve_forever()
