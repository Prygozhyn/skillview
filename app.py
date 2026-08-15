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
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import describe
import inventory
import notes
import updater
import upstream

HERE = Path(__file__).parent
CONFIG = json.loads((HERE / "config.json").read_text()) if (HERE / "config.json").exists() else {}
PORT = int(os.environ.get("PORT") or CONFIG.get("port", 8477))

# Running update commands is off unless the operator asks for it. A clone that
# is merely being looked at has no write surface: the endpoint refuses, and the
# UI never offers the button.
UPDATES = ("--enable-updates" in sys.argv
           or CONFIG.get("updates") is True
           or os.environ.get("SKILLVIEW_ENABLE_UPDATES") == "1")


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
            self._send({"items": items(), "cli": describe.available(), "updates": UPDATES})
        elif self.path == "/api/refresh":
            self._send({"items": items(with_upstream=True), "cli": describe.available(), "updates": UPDATES})
        elif self.path.startswith("/api/catalog"):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            repo, path = q.get("repo", [""])[0], q.get("path", [""])[0]
            if not repo or not path:
                self._send({"error": "repo and path are required", "skills": []}, 400)
                return
            rows = inventory.inventory()
            installed = [r["source_path"] for r in rows
                         if r["source_repo"] == repo and r["source_path"]]
            self._send(upstream.catalog(repo, path, installed))
        elif self.path == "/api/notes":
            self._send({"notes": notes.get_all()})
        else:
            self._send({"error": "not found"}, 404)

    def _body(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n) or "{}")
        except (ValueError, OSError):
            return {}

    def do_POST(self):
        if self.path == "/api/describe":
            rows = inventory.inventory()
            added, err = describe.generate(rows)
            self._send({"added": added, "error": err})
        elif self.path == "/api/update":
            if not UPDATES:
                self._send({"ok": False, "error":
                            "Updates are disabled. Restart with --enable-updates to allow them."}, 403)
                return
            # The request selects a row; it never supplies a command. Anything
            # that does not match a live inventory row is refused here, before
            # updater.py is reached.
            name = self._body().get("name")
            row = next((r for r in inventory.inventory() if r["name"] == name), None)
            if row is None:
                self._send({"ok": False, "error": "No installed item by that name."}, 404)
                return
            self._send(updater.update(row))
        elif self.path == "/api/notes":
            # No UPDATES gate: this writes one local text file and never
            # shells out, so it carries none of updater.py's risk.
            body = self._body()
            name = body.get("name")
            if not name:
                self._send({"error": "name is required"}, 400)
                return
            self._send({"notes": notes.set_note(name, str(body.get("text", "")))})
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
