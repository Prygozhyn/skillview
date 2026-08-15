#!/usr/bin/env python3
"""Checks on upstream.py's network-facing functions.

Fixture-driven, no framework. Network calls go through upstream._get, so
stubbing that one function is the seam — same principle as test_updater.py
stubbing subprocess.run rather than mocking internals deeper in the call.
"""
import upstream


def stub_get(responses):
    """Return a fake _get that answers by URL substring, records calls."""
    calls = []

    def fake(url, parse_json=True):
        calls.append(url)
        for needle, body in responses.items():
            if needle in url:
                return body
        return None
    return fake, calls


MARKETPLACE_JSON = {
    "plugins": [
        {"name": "claude-mem", "description": "Persist context across sessions.",
         "category": "productivity"},
        {"name": "claude-mem-lite", "description": "A smaller variant.",
         "category": "productivity"},
        {"name": "claude-mem-pro", "description": "The paid tier.",
         "category": "productivity"},
    ]
}


def main():
    real_get = upstream._get

    # --- the repo lists every plugin it ships, installed ones flagged ------
    upstream._get, calls = stub_get({"marketplace.json": MARKETPLACE_JSON})
    try:
        d = upstream.marketplace_catalog("thedotmack/claude-mem", {"claude-mem"})
    finally:
        upstream._get = real_get

    plugins = d.get("plugins", [])
    assert len(plugins) == 3, plugins
    by_name = {p["name"]: p for p in plugins}
    assert by_name["claude-mem"]["installed"] is True
    assert by_name["claude-mem-lite"]["installed"] is False
    assert by_name["claude-mem-pro"]["installed"] is False
    assert by_name["claude-mem-lite"]["description"] == "A smaller variant."
    assert len(calls) == 1, "must fetch the manifest exactly once, not per plugin"

    # --- an empty installed set flags nothing, rather than raising ---------
    upstream._get, _ = stub_get({"marketplace.json": MARKETPLACE_JSON})
    try:
        d = upstream.marketplace_catalog("thedotmack/claude-mem", set())
    finally:
        upstream._get = real_get
    assert all(p["installed"] is False for p in d["plugins"])

    # --- an unreachable manifest is reported, not a crash -------------------
    upstream._get, _ = stub_get({})
    try:
        d = upstream.marketplace_catalog("someone/nonexistent", {"x"})
    finally:
        upstream._get = real_get
    assert d.get("skills", d.get("plugins")) == [] or "error" in d, d
    assert "error" in d, "an unreachable repo must say so, not return an empty success"

    # --- catalog() must use the frontmatter name, not the folder, for the
    # thing you'd actually pass to `skills add --skill` ---------------------
    # Real case that failed live: Leonxlnx/taste-skill's folder "output-skill"
    # declares itself "full-output-enforcement" in SKILL.md. Sending the
    # folder name to the install command 404s — "No matching skills found".
    TREE = {"tree": [
        {"path": "skills", "type": "tree", "sha": "a"},
        {"path": "skills/output-skill", "type": "tree", "sha": "b"},
        {"path": "skills/other-skill", "type": "tree", "sha": "c"},
    ]}
    SKILL_MD = {
        "skills/output-skill/SKILL.md":
            "---\nname: full-output-enforcement\ndescription: Enforces complete output.\n---\n",
        "skills/other-skill/SKILL.md":
            "---\nname: other-skill\ndescription: Something else.\n---\n",
    }

    def fake_get(url, parse_json=True):
        if "git/trees" in url:
            return TREE
        for path, body in SKILL_MD.items():
            if path in url:
                return body
        return None

    upstream._get = fake_get
    try:
        d = upstream.catalog("Leonxlnx/taste-skill", "skills/output-skill/SKILL.md")
    finally:
        upstream._get = real_get

    by_name = {s["name"]: s for s in d["skills"]}
    assert by_name["output-skill"]["install_name"] == "full-output-enforcement", \
        by_name["output-skill"]
    assert by_name["other-skill"]["install_name"] == "other-skill"

    print("ok — marketplace_catalog lists siblings and flags installed ones")
    print("ok — catalog() surfaces the frontmatter name, not the folder, for install")


if __name__ == "__main__":
    main()
