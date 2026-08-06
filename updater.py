#!/usr/bin/env python3
"""The only code in Skillview that can change anything. Read it in full.

Disabled by default. Nothing here runs unless the operator started the server
with --enable-updates, so a clone that is merely being looked at has no write
surface at all.

Safety properties, each of which the tests assert:

1. No shell, ever. Every command is an argv list run with shell=False, so
   quoting, globbing, pipes, and `;` have no meaning.
2. The page cannot supply a command. A request names a skill; that name is
   looked up in the live inventory and the *row's own* values build the argv.
   A name that matches no row is refused before anything executes.
3. Only known mechanisms have commands. Anything else is refused with the
   manual instruction, never guessed at.
4. One update at a time, under a lock, with a timeout.
"""
import re
import shutil
import subprocess
import threading

TIMEOUT = 300
_lock = threading.Lock()

# These tools colour their output even when not attached to a terminal, and the
# raw escapes render as literal garbage in the panel.
ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


def clean(text):
    return ANSI.sub("", text or "").strip()

# mechanism -> (binary, [argv builders]). Each builder takes the inventory row
# and returns a complete argv list. Adding a mechanism means adding a row here;
# there is deliberately no generic escape hatch.
PLANS = {
    "marketplace": ("claude", [
        lambda r: ["claude", "plugin", "marketplace", "update", r["marketplace"]],
        lambda r: ["claude", "plugin", "update", r["name"]],
    ]),
    "skills.sh": ("npx", [
        lambda r: ["npx", "--yes", "skills@latest", "update", r["name"]],
    ]),
}

# Mechanisms that only take effect after Claude Code restarts (P4).
NEEDS_RESTART = {"marketplace"}


def plan(row):
    """The exact argv lists that would run, or [] if this row is not updatable."""
    entry = PLANS.get(row.get("mechanism"))
    if not entry:
        return []
    _binary, builders = entry
    try:
        argvs = [b(row) for b in builders]
    except KeyError:
        return []
    # A missing field would otherwise produce an argv with an empty element.
    return argvs if all(all(part for part in a) for a in argvs) else []


def update(row):
    """Run the plan for one row. Returns a result dict; never raises."""
    mech = row.get("mechanism")
    entry = PLANS.get(mech)
    if not entry:
        return _fail(f"Skillview does not know how to update a '{mech}' install. "
                     "Update it manually with the command shown.")
    binary, _builders = entry
    if not shutil.which(binary):
        return _fail(f"`{binary}` is not on PATH, so this update cannot run here.")

    argvs = plan(row)
    if not argvs:
        return _fail("This item is missing the details needed to build an update command.")

    if not _lock.acquire(blocking=False):
        return _fail("Another update is already running. Wait for it to finish.")
    try:
        log = []
        for argv in argvs:
            log.append("$ " + " ".join(argv))
            try:
                p = subprocess.run(argv, capture_output=True, text=True,
                                   timeout=TIMEOUT, shell=False)
            except subprocess.TimeoutExpired:
                log.append(f"timed out after {TIMEOUT}s")
                return _fail("The update timed out.", log)
            except OSError as e:
                log.append(str(e))
                return _fail("The update command could not be started.", log)
            out = clean(p.stdout)
            err = clean(p.stderr)
            if out:
                log.append(out)
            if err:
                log.append(err)
            if p.returncode != 0:
                return _fail(f"`{argv[0]}` exited {p.returncode}.", log)
        return {
            "ok": True,
            "restart_required": mech in NEEDS_RESTART,
            "log": "\n".join(log),
            "error": "",
        }
    finally:
        _lock.release()


def _fail(message, log=None):
    return {"ok": False, "restart_required": False,
            "log": "\n".join(log or []), "error": message}


if __name__ == "__main__":
    # Show what would run for every installed item, without running anything.
    from inventory import inventory
    for r in inventory():
        argvs = plan(r)
        shown = "  ;  ".join(" ".join(a) for a in argvs) if argvs else "— not updatable"
        print(f"{r['name']:28} {r['mechanism']:12} {shown}")
