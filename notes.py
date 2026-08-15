#!/usr/bin/env python3
"""Per-item notes — the first piece of user-owned mutable state.

Not gated behind --enable-updates: this writes one local text file and never
shells out, so it carries none of updater.py's risk. A reason-for-keeping or
a "delete this next cleanup" surviving between sessions was the whole point.

Cache is plain JSON, same as descriptions.json — hand-editable, and every
write re-reads first, so a second writer's change never gets clobbered by one
still holding a stale copy in memory.
"""
import json
from pathlib import Path

CACHE = Path(__file__).parent / "notes.json"


def get_all():
    try:
        return json.loads(CACHE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def set_note(name, text):
    """Upsert one item's note. An empty note removes the key rather than
    leaving clutter behind."""
    current = get_all()
    if text.strip():
        current[name] = text
    else:
        current.pop(name, None)
    CACHE.write_text(json.dumps(current, indent=1, sort_keys=True, ensure_ascii=False),
                     encoding="utf-8")
    return current
