#!/usr/bin/env python3
"""Skillview — one table for every skill, plugin and tool your agent can reach.

    python3 app.py        ->  http://localhost:8477

Binds to localhost only. v1 is read-only: it reads local state, checks upstream,
and reports. It does not run update commands — that is v2, and it is deferred
deliberately, because a web server that shells out is a different security
posture than one that does not.

This file is a thin HTTP shim by rule: parse a path, call a pure function in
inventory/upstream/describe, serialise the result. No product logic lives here,
so swapping the web layer later costs a day and touches nothing else.
"""
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import describe
import inventory
import upstream

HERE = Path(__file__).parent
CONFIG = json.loads((HERE / "config.json").read_text()) if (HERE / "config.json").exists() else {}
PORT = int(os.environ.get("PORT") or CONFIG.get("port", 8477))


def items(with_upstream=False):
    rows = inventory.inventory()
    if with_upstream:
        rows = upstream.check(rows)
    else:
        for r in rows:
            r["update"], r["update_command"], r["catalog"] = "", "", []
    return describe.apply(rows)


class Handler(BaseHTTPRequestHandler):
    def _send(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/":
            page = (HERE / "ui.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(page)))
            self.end_headers()
            self.wfile.write(page)
        elif self.path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
        elif self.path == "/api/items":
            self._send({"items": items(), "cli": describe.available()})
        elif self.path == "/api/refresh":
            self._send({"items": items(with_upstream=True), "cli": describe.available()})
        else:
            self._send({"error": "not found"}, 404)

    def do_POST(self):
        if self.path == "/api/describe":
            rows = inventory.inventory()
            added, err = describe.generate(rows)
            self._send({"added": added, "error": err})
        else:
            self._send({"error": "not found"}, 404)

    def log_message(self, *a):
        pass  # the terminal is for errors, not a request log


def main():
    if "--scan" in sys.argv:          # headless sanity check, no browser needed
        for r in items(with_upstream="--net" in sys.argv):
            print(f"{r['name']:28} {r['mechanism']:12} {r.get('update', ''):16} {r['plain'][:60]}")
        return
    try:
        server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    except OSError as e:
        # Port already taken is the likeliest first-run failure. A traceback
        # here reads as "this is broken" rather than "pick another port".
        print(f"Could not start on port {PORT}: {e}\n"
              f"Something else is using it. Try:  PORT=8478 python3 {Path(__file__).name}\n"
              f"or change \"port\" in config.json.")
        raise SystemExit(1)
    print(f"skillview -> http://localhost:{PORT}   (ctrl-c to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()
