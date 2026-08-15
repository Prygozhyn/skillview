#!/usr/bin/env python3
"""Checks on the only code that can change anything. Run: python3 test_updater.py

These assert the safety properties updater.py claims, not just that a happy
path works. Nothing here executes a real update — the subprocess call is
replaced, and what it was *asked* to run is inspected.
"""
import subprocess
import updater
from inventory import row


class Recorder:
    """Stands in for subprocess.run and remembers the call."""
    def __init__(self, returncode=0, stdout="done", stderr=""):
        self.calls, self.kwargs = [], []
        self.rc, self.out, self.err = returncode, stdout, stderr

    def __call__(self, argv, **kw):
        self.calls.append(argv)
        self.kwargs.append(kw)
        return subprocess.CompletedProcess(argv, self.rc, self.out, self.err)


def with_stub(rec, present=True):
    updater.subprocess.run = rec
    updater.shutil.which = lambda b: "/usr/bin/" + b if present else None


def restore():
    updater.subprocess.run = subprocess.run
    import shutil
    updater.shutil.which = shutil.which


def plugin(name="demo", mkt="somemarket"):
    return row(name=name, mechanism="marketplace", marketplace=mkt, kind="plugin")


def pack(name="someskill"):
    return row(name=name, mechanism="skills.sh")


def main():
    # A plan exists only for mechanisms we actually know.
    assert updater.plan(plugin()), "marketplace should be updatable"
    assert updater.plan(pack()), "skills.sh should be updatable"
    for mech in ("local", "mcp", "unknown", "", "made-up"):
        assert updater.plan(row(name="x", mechanism=mech)) == [], mech

    # A row missing the field its argv needs produces no plan, rather than an
    # argv with an empty element in it.
    assert updater.plan(plugin(mkt="")) == [], "empty marketplace must not build an argv"

    rec = Recorder()
    with_stub(rec)
    try:
        # --- shell is never used, and args stay separate list elements -------
        res = updater.update(pack("obsidian-cli"))
        assert res["ok"], res
        assert rec.calls == [["npx", "--yes", "skills@latest", "update", "obsidian-cli"]], rec.calls
        assert all(kw.get("shell") is False for kw in rec.kwargs), "shell=True is never allowed"
        assert all("timeout" in kw for kw in rec.kwargs), "every call must be bounded"

        # --- a hostile name cannot become a second command ------------------
        # It has already been looked up in the inventory by app.py, but even if
        # one arrived, it stays a single argv element with no shell to parse it.
        rec.calls.clear()
        nasty = "; rm -rf ~ #"
        updater.update(pack(nasty))
        assert rec.calls == [["npx", "--yes", "skills@latest", "update", nasty]], rec.calls
        assert len(rec.calls[0]) == 5, "injection must not split into extra argv elements"

        # --- marketplace runs both steps, in order, and asks for a restart ---
        rec.calls.clear()
        res = updater.update(plugin("ponytail", "ponytail"))
        assert rec.calls == [
            ["claude", "plugin", "marketplace", "update", "ponytail"],
            # Scoped "<plugin>@<marketplace>", matching `claude plugin list`'s
            # own naming — the bare plugin name 404s even after a successful
            # marketplace refresh, confirmed against the real CLI.
            ["claude", "plugin", "update", "ponytail@ponytail"],
        ], rec.calls
        assert res["restart_required"] is True
        assert updater.update(pack())["restart_required"] is False

        # --- colour codes never reach the panel ------------------------------
        # npx colours its output even when not attached to a terminal.
        coloured = Recorder(stdout="\x1b[38;5;145mUpdating…\x1b[0m\n\x1b[K\x1b[32mdone\x1b[0m")
        with_stub(coloured)
        res = updater.update(pack())
        assert "\x1b" not in res["log"], repr(res["log"])
        assert "Updating…" in res["log"] and "done" in res["log"], res["log"]

        # --- a failing step stops the sequence -------------------------------
        bad = Recorder(returncode=1, stdout="", stderr="boom")
        with_stub(bad)
        res = updater.update(plugin())
        assert not res["ok"] and "exited 1" in res["error"], res
        assert len(bad.calls) == 1, "must not run step 2 after step 1 fails"
        assert "boom" in res["log"]

        # --- an unknown mechanism refuses before touching subprocess ---------
        never = Recorder()
        with_stub(never)
        res = updater.update(row(name="graphify", mechanism="unknown"))
        assert not res["ok"] and never.calls == [], "unknown mechanism must not execute"
        assert "manually" in res["error"]

        # --- a missing binary refuses with a clear reason --------------------
        with_stub(Recorder(), present=False)
        res = updater.update(pack())
        assert not res["ok"] and "not on PATH" in res["error"], res

        # --- a held lock refuses rather than running concurrently ------------
        with_stub(Recorder())
        updater._lock.acquire()
        try:
            res = updater.update(pack())
            assert not res["ok"] and "already running" in res["error"], res
        finally:
            updater._lock.release()
        # ...and the lock is free again afterwards.
        assert updater.update(pack())["ok"], "lock must be released on the happy path"
    finally:
        restore()

    # --- install(): the greyed-row install button (D3/P6) ------------------
    # Same write surface, same safety properties, different verb: the row
    # doesn't exist in inventory yet (that's the whole point), so the request
    # supplies structured data — kind, repo, name, marketplace — and the argv
    # is built entirely server-side from that, never from a raw command.
    rec = Recorder()
    with_stub(rec)
    try:
        res = updater.install("skill", "kepano/obsidian-skills", "defuddle", "")
        assert res["ok"], res
        assert rec.calls == [["npx", "--yes", "skills@latest", "add",
                              "kepano/obsidian-skills", "--skill", "defuddle", "-g"]], rec.calls
        assert res["restart_required"] is False, "a skill install matches skill updates: no restart"

        rec.calls.clear()
        res = updater.install("plugin", "thedotmack/claude-mem", "claude-mem", "thedotmack")
        assert rec.calls == [["claude", "plugin", "install", "claude-mem@thedotmack"]], rec.calls
        assert res["restart_required"] is True, "a plugin install matches plugin updates: needs restart"

        # A plugin install with no marketplace can't build a safe scoped
        # command, so it refuses rather than guessing at an unscoped one.
        rec.calls.clear()
        res = updater.install("plugin", "thedotmack/claude-mem", "claude-mem", "")
        assert not res["ok"] and rec.calls == [], res

        # An unrecognised kind refuses before touching subprocess.
        rec.calls.clear()
        res = updater.install("gadget", "x/y", "z", "")
        assert not res["ok"] and rec.calls == [], res

        # Injection safety holds the same way it does for update(): a hostile
        # name stays one argv element, never shell-parsed.
        rec.calls.clear()
        nasty = "; rm -rf ~ #"
        updater.install("skill", "some/repo", nasty, "")
        assert rec.calls == [["npx", "--yes", "skills@latest", "add",
                              "some/repo", "--skill", nasty, "-g"]], rec.calls
        assert len(rec.calls[0]) == 8, "injection must not split into extra argv elements"

        # install() and update() share the one lock — an install cannot run
        # while an update is in flight, or vice versa.
        updater._lock.acquire()
        try:
            res = updater.install("skill", "kepano/obsidian-skills", "defuddle", "")
            assert not res["ok"] and "already running" in res["error"], res
        finally:
            updater._lock.release()
    finally:
        restore()

    print("ok — updater safety properties hold, including install()")


if __name__ == "__main__":
    main()
