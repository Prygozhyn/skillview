#!/usr/bin/env python3
"""Plain-English descriptions, generated once and cached.

A SKILL.md `description:` is written to make a model trigger the skill. It is
not written to tell you what the thing does for you, and it reads like keyword
soup. This turns that into one honest sentence per tool.

Generation shells out to the `claude` CLI already on the machine — no API key,
no account setup, nothing for a stranger to configure. If the CLI is absent the
table still renders from raw frontmatter with a visible note, so this is an
enhancement and never a dependency.

Cache is keyed by name+version, so a description goes stale exactly when the
tool it describes changes. The file is plain JSON — edit any line by hand and
it will be kept.
"""
import json
import os
import shutil
import subprocess
from pathlib import Path

CACHE = Path(__file__).parent / "descriptions.json"
TIMEOUT = 180

PROMPT = """For each tool below, write ONE sentence saying what it does FOR THE \
USER — what they get, in plain language a non-expert understands.

Rules:
- Explain the benefit, not the mechanism. "Pulls clean text out of a web page so \
you're not reading through ads and nav junk" — not "extracts content via CLI".
- No jargon, no marketing, no restating the name.
- Under 25 words.
- If the input description is keyword soup, ignore it and infer from the name.

Return ONLY a JSON object mapping each tool's exact name to its sentence. No \
markdown fence, no commentary.

Tools:
"""


def available():
    return shutil.which("claude") is not None


def load():
    try:
        return json.loads(CACHE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save(cache):
    CACHE.write_text(json.dumps(cache, indent=2, sort_keys=True), encoding="utf-8")


def key(row):
    """Cache key that actually changes when the tool changes.

    Version alone is not enough: skills.sh installs carry no version at all, so
    keying on it alone pins every one of them to a single bucket and their
    descriptions never refresh after an update. The folder hash moves whenever
    the skill's content does, which is exactly the invalidation we want.
    """
    stamp = row.get("version") or (row.get("folder_hash") or "")[:12] or "-"
    return f"{row['name']}@{stamp}"


def migrate(cache, rows):
    """Carry entries forward when a row gains a stamp it previously lacked.

    Without this, changing the key silently throws away every cached sentence
    and bills the user for a regeneration they did not ask for.
    """
    moved = 0
    for r in rows:
        new, old = key(r), f"{r['name']}@-"
        if new != old and old in cache and new not in cache:
            cache[new] = cache.pop(old)
            moved += 1
    return moved


def apply(rows, cache=None):
    """Attach `plain` to each row. Falls back to frontmatter, flagged as such."""
    owned = cache is None
    cache = load() if owned else cache
    if owned and migrate(cache, rows):
        save(cache)
    for r in rows:
        hit = cache.get(key(r))
        r["plain"] = hit or r.get("description", "")
        r["plain_generated"] = bool(hit)
    return rows


def stale(rows, cache=None):
    cache = load() if cache is None else cache
    return [r for r in rows if key(r) not in cache]


def generate(rows):
    """One batched CLI call for everything uncached. Returns (added, error)."""
    if not available():
        return 0, "The `claude` CLI is not on PATH — showing raw descriptions instead."
    cache = load()
    todo = stale(rows, cache)
    if not todo:
        return 0, ""

    listing = "\n".join(
        f"- {r['name']}: {(r.get('description') or '(no description)')[:300]}"
        for r in todo
    )
    try:
        proc = subprocess.run(
            ["claude", "-p", PROMPT + listing],
            capture_output=True, text=True, timeout=TIMEOUT,
            cwd=str(Path(__file__).parent),
            env={**os.environ, "CLAUDE_MEM_DISABLE": "1"},
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        return 0, f"Description generation failed: {e}"

    # The CLI reports refusals on stdout, not stderr — reading only stderr turns
    # "Not logged in" into a bare exit code.
    said = (proc.stderr.strip() or proc.stdout.strip())[:300]
    if "not logged in" in said.lower():
        return 0, "The `claude` CLI is not logged in. Run `claude` once in a terminal and sign in, then try again."
    if proc.returncode != 0:
        return 0, f"claude exited {proc.returncode}: {said}" if said else f"claude exited {proc.returncode}."

    parsed = _extract_json(proc.stdout)
    if not parsed:
        return 0, f"Could not parse a JSON object from the CLI response: {said[:160]}"

    by_name = {r["name"]: r for r in todo}
    added = 0
    for name, sentence in parsed.items():
        row = by_name.get(name)
        if row and isinstance(sentence, str) and sentence.strip():
            cache[key(row)] = sentence.strip()
            added += 1
    save(cache)
    return added, ""


def _extract_json(text):
    """The CLI may wrap the object in prose or a fence. Take the outermost {}."""
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        obj = json.loads(text[start:end + 1])
        return obj if isinstance(obj, dict) else None
    except ValueError:
        return None


if __name__ == "__main__":
    from inventory import inventory
    rows = inventory()
    n, err = generate(rows)
    print(err or f"generated {n} descriptions -> {CACHE.name}")
    for r in apply(rows):
        mark = "*" if r["plain_generated"] else " "
        print(f"{mark} {r['name']:26} {r['plain'][:88]}")
