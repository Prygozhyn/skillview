#!/usr/bin/env python3
"""How often each item has actually been invoked.

Read-only, like notes.py but simpler still: nothing here writes. The tally is
produced outside Skillview by ~/.claude/skill-usage/collect.py, which reads
Claude Code's own session transcripts. Skillview instruments nothing and ships
no hook — it reads a file that already exists.

Absent is not the same as zero. A skill missing from the tally means "no
invocation in the transcripts ever scanned", and that window starts at the
oldest transcript still on disk — Claude Code deletes sessions past
cleanupPeriodDays (30 by default), so the record has a floor. Callers must not
present a blank as proof of never.
"""
import json
import os
from datetime import datetime
from pathlib import Path

CACHE = Path.home() / ".claude" / "skill-usage" / "usage.json"


def _load():
    try:
        return json.loads(CACHE.read_text(encoding="utf-8")).get("skills", {})
    except (OSError, ValueError):
        return {}


def _merge(into, entry):
    """Several tally keys can land on one row — a plugin is one row but each
    bundled skill is invoked under its own name."""
    if into is None:
        return {"count": entry.get("count", 0),
                "first_used": entry.get("first_used", ""),
                "last_used": entry.get("last_used", "")}
    into["count"] += entry.get("count", 0)
    for key, pick in (("first_used", min), ("last_used", max)):
        a, b = into.get(key) or "", entry.get(key) or ""
        into[key] = pick(a, b) if a and b else (a or b)
    return into


def apply(rows):
    """Fill row["usage"]; return (rows, summary).

    A plugin-scoped key rolls up to its plugin's row rather than falling back
    to the bare name: "anthropic-skills:log" is not necessarily your own "log"
    skill, and attributing one to the other would be worse than reporting neither.
    """
    by_name = {r["name"]: r for r in rows}
    unmatched = []
    for key, entry in sorted(_load().items()):
        target = by_name.get(entry.get("plugin") or key)
        if target is None:
            unmatched.append(key)
            continue
        target["usage"] = _merge(target.get("usage"), entry)
    return rows, {"collected": _collected(), "unmatched": unmatched}


def _collected():
    """When the tally was last written, so a stale file is visible as stale."""
    try:
        # Local date deliberately: this is shown next to "last collected", and
        # a UTC date reads as yesterday for anyone east of Greenwich.
        return datetime.fromtimestamp(os.path.getmtime(CACHE)).date().isoformat()
    except OSError:
        return ""


def demo():
    rows = [{"name": "note", "usage": None}, {"name": "mattpocock-skills", "usage": None}]
    global _load
    real, _load = _load, lambda: {
        "note": {"count": 4, "first_used": "2026-08-10", "last_used": "2026-08-26", "plugin": None},
        "mattpocock-skills:tdd": {"count": 1, "first_used": "2026-08-15",
                                  "last_used": "2026-08-15", "plugin": "mattpocock-skills"},
        "mattpocock-skills:grilling": {"count": 2, "first_used": "2026-08-18",
                                       "last_used": "2026-08-18", "plugin": "mattpocock-skills"},
        "anthropic-skills:log": {"count": 1, "first_used": "2026-07-31",
                                 "last_used": "2026-07-31", "plugin": "anthropic-skills"},
    }
    try:
        out, summary = apply(rows)
    finally:
        _load = real
    assert out[0]["usage"] == {"count": 4, "first_used": "2026-08-10", "last_used": "2026-08-26"}
    # Two bundled skills roll up into the one plugin row, dates spanning both.
    assert out[1]["usage"] == {"count": 3, "first_used": "2026-08-15", "last_used": "2026-08-18"}
    # An uninstalled plugin's key never leaks onto the similarly-named local skill.
    assert summary["unmatched"] == ["anthropic-skills:log"]
    print("usage: ok")


if __name__ == "__main__":
    demo()
