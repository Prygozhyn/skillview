#!/usr/bin/env python3
"""Fixture-driven checks for inventory.py.  Run: python3 test_inventory.py

No framework. Each case builds a throwaway $HOME, points the scanner at it, and
asserts. The cases that matter are the ugly ones — an empty machine and a
SKILL.md that real YAML refuses to parse — because those are what a stranger's
first run looks like when it goes wrong.
"""
import json
import os
import shutil
import tempfile
from pathlib import Path

import inventory


def build(root, *, plugins=False, lock=False, local=False, broken=False, versioned=False):
    """Compose a fake $HOME containing only the mechanisms asked for."""
    home = Path(root)
    if plugins:
        cache = home / ".claude/plugins/cache/mkt/demo/2.0.0"
        (cache / ".claude-plugin").mkdir(parents=True)
        (cache / ".claude-plugin/plugin.json").write_text(json.dumps({
            "name": "demo", "version": "2.0.0", "description": "A demo plugin.",
            "author": {"name": "Some Author"},
        }))
        (cache / "skills/alpha").mkdir(parents=True)
        (cache / "skills/alpha/SKILL.md").write_text(
            "---\nname: alpha\ndescription: Does alpha things.\n---\nBody.\n")
        (cache / "commands").mkdir()
        (cache / "commands/beta.toml").write_text("")
        (cache / "hooks").mkdir()
        (home / ".claude/plugins").mkdir(parents=True, exist_ok=True)
        (home / ".claude/plugins/installed_plugins.json").write_text(json.dumps({
            "plugins": {"demo@mkt": [{"installPath": str(cache), "version": "2.0.0",
                                      "installedAt": "2026-01-01T00:00:00Z"}]}}))
        (home / ".claude/plugins/known_marketplaces.json").write_text(json.dumps({
            "mkt": {"source": {"source": "github", "repo": "someone/mkt"}}}))
    if lock:
        (home / ".agents").mkdir(parents=True, exist_ok=True)
        (home / ".agents/.skill-lock.json").write_text(json.dumps({
            "version": 3, "skills": {"tracked": {
                "source": "vendor/pack", "sourceType": "github",
                "sourceUrl": "https://github.com/vendor/pack.git",
                "skillPath": "skills/tracked/SKILL.md",
                "skillFolderHash": "abc123", "installedAt": "2026-01-01T00:00:00Z"}}}))
        d = home / ".claude/skills/tracked"
        d.mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_text(
            "---\nname: tracked\ndescription: From a pack.\n---\nBody.\n")
    if local:
        d = home / ".claude/skills/mine"
        d.mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_text(
            "---\nname: mine\ndescription: I wrote this.\ndisable-model-invocation: true\n---\nBody.\n")
    if broken:
        # Real case: a description containing ': ' — this exact shape makes
        # `npx skills list` emit a YAML parse error and skip the file.
        d = home / ".claude/skills/awkward"
        d.mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_text(
            "---\nname: awkward\ndescription: Answer things: books, talks, notes.\n---\n"
            "Needs `npm install -g widget` first.\n")
    if versioned:
        d = home / ".claude/skills/vendored"
        d.mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_text("---\nname: vendored\ndescription: X.\n---\nBody.\n")
        (d / ".vendored_version").write_text("1.2.3\n")
    return home


def scan_in(home):
    """Run the scanner as if $HOME were the fixture."""
    old_home, old_cfg = os.environ.get("HOME"), os.environ.pop("CLAUDE_CONFIG_DIR", None)
    os.environ["HOME"] = str(home)
    Path.home.cache_clear() if hasattr(Path.home, "cache_clear") else None
    try:
        return inventory.inventory()
    finally:
        if old_home:
            os.environ["HOME"] = old_home
        if old_cfg:
            os.environ["CLAUDE_CONFIG_DIR"] = old_cfg


def case(name, **kw):
    tmp = tempfile.mkdtemp()
    try:
        return scan_in(build(tmp, **kw)), tmp
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    # C4 — the empty machine. A stranger's first run must not raise.
    rows, _ = case("empty")
    assert rows == [], f"empty machine should scan to [], got {len(rows)} rows"

    # Plugins expand into what they ship (R23).
    rows, _ = case("plugins", plugins=True)
    assert len(rows) == 1, f"expected 1 plugin row, got {len(rows)}"
    p = rows[0]
    assert p["mechanism"] == "marketplace"
    assert p["author"] == "Some Author", p["author"]
    assert p["source_repo"] == "someone/mkt", p["source_repo"]
    kinds = sorted(s["kind"] for s in p["subcommands"])
    assert kinds == ["command", "hook", "skill"], kinds

    # A lockfile with no plugins present — mechanisms are independent (C4).
    rows, _ = case("lock only", lock=True)
    assert [r["name"] for r in rows] == ["tracked"], rows
    t = rows[0]
    assert t["mechanism"] == "skills.sh"
    assert t["author"] == "vendor", t["author"]
    assert t["folder_hash"] == "abc123"
    assert t["is_local"] is False

    # Local authorship is derived from absent upstream, never a name (C3, R12).
    rows, _ = case("local", local=True)
    m = rows[0]
    assert m["is_local"] is True, m
    assert m["mechanism"] == "local"
    assert m["activation"] == "manual", m["activation"]  # disable-model-invocation

    # The frontmatter a YAML parser rejects must still scan, and its prereq
    # must still be found (R17).
    rows, _ = case("broken", broken=True)
    a = rows[0]
    assert a["name"] == "awkward", a["name"]
    assert a["description"].startswith("Answer things"), a["description"]
    assert a["activation"] == "needs starting", a["activation"]
    assert a["prereqs"] == ["npm install -g widget"], a["prereqs"]

    # A version file from an unrecognised installer: detected, not identified (C9).
    rows, _ = case("versioned", versioned=True)
    v = rows[0]
    assert v["mechanism"] == "unknown", v["mechanism"]
    assert v["version"] == "1.2.3", v["version"]
    assert v["author"] == "", "unknown source must not be attributed to the user"
    assert v["is_local"] is False

    # Everything at once — a tracked skill must not also appear as local.
    rows, _ = case("all", plugins=True, lock=True, local=True, broken=True, versioned=True)
    names = [r["name"] for r in rows]
    assert len(names) == len(set(names)), f"duplicate rows: {names}"
    assert set(names) == {"demo", "tracked", "mine", "awkward", "vendored"}, names

    print(f"ok — {len(names)} rows in the combined fixture, all cases pass")


if __name__ == "__main__":
    main()
