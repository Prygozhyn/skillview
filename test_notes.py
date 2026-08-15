#!/usr/bin/env python3
"""Checks on notes.py — the first piece of user-owned mutable state.

Fixture-driven, no framework, matching test_inventory.py and test_updater.py.
Each case swaps notes.CACHE to a throwaway path so runs never touch the real
notes.json.
"""
import tempfile
from pathlib import Path

import notes


def with_tmp_store(fn):
    tmp = Path(tempfile.mkdtemp()) / "notes.json"
    old = notes.CACHE
    notes.CACHE = tmp
    try:
        fn(tmp)
    finally:
        notes.CACHE = old


def main():
    # A store that has never been written to reads as empty, not an error —
    # same absent-tolerance the rest of the project holds everywhere else.
    def case_missing_file(tmp):
        assert notes.get_all() == {}
    with_tmp_store(case_missing_file)

    # Set, then read back — the basic round trip.
    def case_round_trip(tmp):
        notes.set_note("defuddle", "keep this, used it twice this week")
        assert notes.get_all() == {"defuddle": "keep this, used it twice this week"}
    with_tmp_store(case_round_trip)

    # A second item accumulates rather than overwriting the first.
    def case_multiple(tmp):
        notes.set_note("defuddle", "note one")
        notes.set_note("graphify", "note two")
        assert notes.get_all() == {"defuddle": "note one", "graphify": "note two"}
    with_tmp_store(case_multiple)

    # Re-setting the same name overwrites rather than duplicating.
    def case_overwrite(tmp):
        notes.set_note("defuddle", "first draft")
        notes.set_note("defuddle", "revised")
        assert notes.get_all() == {"defuddle": "revised"}
    with_tmp_store(case_overwrite)

    # Clearing a note back to empty removes the key rather than leaving a
    # blank entry behind — the file should not grow forever with clutter.
    def case_clear_removes(tmp):
        notes.set_note("defuddle", "temporary")
        notes.set_note("defuddle", "")
        assert notes.get_all() == {}
    with_tmp_store(case_clear_removes)

    # Multiline and unicode content survives the round trip untouched — this
    # is freeform user text, not a constrained field.
    def case_freeform_text(tmp):
        text = "line one\nline two — €£¥ 你好"
        notes.set_note("some-skill", text)
        assert notes.get_all()["some-skill"] == text
    with_tmp_store(case_freeform_text)

    # A second process's write is not clobbered by one that started reading
    # first — set_note re-reads before merging in its own change rather than
    # writing back a stale full dict.
    def case_concurrent_set(tmp):
        notes.set_note("a", "first")
        # Simulate a second writer landing between another writer's read and
        # write by mutating the file directly, the way a second request would.
        import json
        on_disk = json.loads(tmp.read_text())
        on_disk["b"] = "from elsewhere"
        tmp.write_text(json.dumps(on_disk))
        notes.set_note("a", "updated")
        assert notes.get_all() == {"a": "updated", "b": "from elsewhere"}
    with_tmp_store(case_concurrent_set)

    print("ok — notes round-trip, overwrite, clear-removes, and merge-on-write all hold")


if __name__ == "__main__":
    main()
