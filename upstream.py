#!/usr/bin/env python3
"""Update status per row. One network call per distinct source repo.

Two comparisons, because the two mechanisms record different things:

    skills.sh    skillFolderHash IS the upstream git tree SHA of the skill's
                 folder — verified against kepano/obsidian-skills. So an exact
                 subtree comparison works; no commit-date guessing.
    marketplace  the marketplace manifest declares a version. Comparing that to
                 the installed version is semantic; comparing commit SHAs would
                 flag every unrelated commit to the repo.

Anything else is reported as detected-but-unchecked with its update command
shown, rather than guessed at. Never claims "up to date" without evidence.

The tree response also enumerates every skill the repo ships. v1 does not
render that, but it is cached — v2's catalog view is then a UI change, not a
new fetch.
"""
import json
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

UA = {"User-Agent": "skillview"}
TIMEOUT = 8

UP_TO_DATE = "up to date"
UPDATE = "update available"
NO_UPSTREAM = "no upstream"
UNKNOWN = "unknown"
MANUAL = "update manually"


def _get(url, parse_json=True):
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            raw = r.read().decode("utf-8", "replace")
        return json.loads(raw) if parse_json else raw
    except (urllib.error.URLError, ValueError, OSError, TimeoutError):
        return None


def fetch_tree(repo):
    """{path: sha} for every directory in the repo, plus the raw entry list."""
    data = _get(f"https://api.github.com/repos/{repo}/git/trees/HEAD?recursive=1")
    if not data or "tree" not in data:
        return None
    return {
        "dirs": {e["path"]: e["sha"] for e in data["tree"] if e["type"] == "tree"},
        "files": [e["path"] for e in data["tree"] if e["type"] == "blob"],
    }


def fetch_marketplace(repo):
    """Declared plugin versions from a marketplace repo.

    marketplace.json is the right place to look but the version field is
    optional and plenty of marketplaces omit it (ponytail's does). When it is
    missing, the plugin's own plugin.json carries the authoritative version, so
    fall back to that before giving up.
    """
    base = f"https://raw.githubusercontent.com/{repo}/HEAD/.claude-plugin"
    versions = {}
    data = _get(f"{base}/marketplace.json")
    if data:
        versions = {p.get("name"): p.get("version") or ""
                    for p in data.get("plugins", [])}
    if not any(versions.values()):
        manifest = _get(f"{base}/plugin.json")
        if manifest and manifest.get("name"):
            versions[manifest["name"]] = manifest.get("version", "")
    return versions or None


def marketplace_catalog(repo, installed_names):
    """Every plugin a marketplace ships, each flagged installed or not.

    Mirrors catalog()'s shape for skills.sh packs (B2/Q9): plugins have no
    source_path to key a git-tree lookup off, but marketplace.json already
    lists every plugin the repo declares in one file, so this needs no tree
    fetch at all — one request regardless of how many plugins it lists.
    """
    base = f"https://raw.githubusercontent.com/{repo}/HEAD/.claude-plugin"
    data = _get(f"{base}/marketplace.json")
    if not data:
        return {"error": "Could not reach the marketplace manifest.", "plugins": []}
    return {"plugins": [{
        "name": p.get("name", ""),
        "description": p.get("description", ""),
        "installed": p.get("name") in installed_names,
    } for p in data.get("plugins", []) if p.get("name")]}


def check(rows):
    """Annotate rows in place with `update` and `update_command`. Returns rows.

    Fetches once per distinct repo, in parallel — a full pass is one call per
    repo regardless of how many skills came from it.
    """
    tree_repos = {r["source_repo"] for r in rows
                  if r["mechanism"] == "skills.sh" and r["source_repo"]}
    mkt_repos = {r["source_repo"] for r in rows
                 if r["mechanism"] == "marketplace" and r["source_repo"]}

    trees, markets = {}, {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        t = {repo: pool.submit(fetch_tree, repo) for repo in tree_repos}
        m = {repo: pool.submit(fetch_marketplace, repo) for repo in mkt_repos}
        trees = {k: f.result() for k, f in t.items()}
        markets = {k: f.result() for k, f in m.items()}

    for r in rows:
        r["update"], r["update_command"], r["catalog"] = _status(r, trees, markets)
    return rows


def _status(r, trees, markets):
    mech = r["mechanism"]

    if mech in ("local", "mcp"):
        # Nothing upstream to be behind. Saying "up to date" here would imply a
        # guarantee that does not exist (R12). An MCP server is a pointer at a
        # command, not a versioned artifact — there is nothing to compare.
        return NO_UPSTREAM, "", []

    if mech == "skills.sh":
        tree = trees.get(r["source_repo"])
        if not tree:
            return UNKNOWN, "", []
        folder = r["source_path"].rsplit("/", 1)[0] if "/" in r["source_path"] else ""
        remote = tree["dirs"].get(folder)
        catalog = _catalog(tree, folder)
        cmd = f"npx skills@latest update {r['name']}"
        if not remote or not r["folder_hash"]:
            return UNKNOWN, cmd, catalog
        return (UP_TO_DATE if remote == r["folder_hash"] else UPDATE), cmd, catalog

    if mech == "marketplace":
        declared = (markets.get(r["source_repo"]) or {}).get(r["name"])
        # Scoped "<plugin>@<marketplace>", matching `claude plugin list`'s own
        # naming (and updater.py's PLANS) — the bare name 404s even right
        # after a successful marketplace refresh, confirmed against the CLI.
        cmd = (f"claude plugin marketplace update && "
               f"claude plugin update {r['name']}@{r['marketplace']}")
        if declared is None or not declared:
            # Manifest reachable but versionless, or unreachable. Either way we
            # have no basis for a claim.
            return UNKNOWN, cmd, []
        return (UP_TO_DATE if declared == r["version"] else UPDATE), cmd, []

    # Unrecognised installer. Detected, and honest about not checking (Q8, C9).
    return MANUAL, "", []


def _catalog(tree, folder):
    """Sibling skill folders in the same pack. Names only — descriptions are
    fetched lazily by catalog(), since most packs are never opened."""
    if not folder or "/" not in folder:
        return []
    parent = folder.rsplit("/", 1)[0]
    return sorted(
        p.rsplit("/", 1)[1] for p in tree["dirs"]
        if p.startswith(parent + "/") and p.count("/") == folder.count("/")
    )


# ------------------------------------------------------- the pack catalog

CATALOG_CACHE = Path(__file__).parent / "catalog.json"


def _cache_read():
    try:
        return json.loads(CATALOG_CACHE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def catalog(repo, sample_path, installed_paths=()):
    """Every skill `repo` ships, each flagged installed or not.

    Called when a detail panel opens, not during refresh — most packs are never
    looked at, and fetching a description per skill for all of them up front
    would cost far more than it returns.

    One tree call plus one raw file per *uninstalled* skill, in parallel. The
    result is cached against the tree SHA, so it is re-fetched only when the
    pack actually changes upstream.
    """
    tree = fetch_tree(repo)
    if not tree:
        return {"error": "Could not reach the repository.", "skills": []}

    parent = sample_path.rsplit("/", 1)[0] if "/" in sample_path else ""
    if "/" in parent:
        parent = parent.rsplit("/", 1)[0]
    depth = sample_path.count("/")
    dirs = sorted(p for p in tree["dirs"]
                  if p.startswith(parent + "/") and p.count("/") == depth - 1)
    if not dirs:
        return {"skills": []}

    stamp = tree["dirs"].get(parent, "")
    cached = _cache_read()
    hit = cached.get(repo)
    descs = hit["descriptions"] if hit and hit.get("stamp") == stamp else {}

    missing = [d for d in dirs if d not in descs]
    if missing:
        with ThreadPoolExecutor(max_workers=8) as pool:
            got = dict(zip(missing, pool.map(lambda d: _description(repo, d), missing)))
        descs.update({k: v for k, v in got.items() if v is not None})
        cached[repo] = {"stamp": stamp, "descriptions": descs}
        try:
            CATALOG_CACHE.write_text(json.dumps(cached, indent=1, sort_keys=True),
                                     encoding="utf-8")
        except OSError:
            pass

    inst = {p.rsplit("/", 1)[0] if p.endswith("SKILL.md") else p for p in installed_paths}
    return {"skills": [{
        "name": d.rsplit("/", 1)[1],
        "path": d,
        "description": descs.get(d, ""),
        "installed": d in inst,
        "url": f"https://github.com/{repo}/tree/HEAD/{d}",
    } for d in dirs]}


def _description(repo, folder):
    """Pull just the frontmatter description from an upstream SKILL.md."""
    raw = _get(f"https://raw.githubusercontent.com/{repo}/HEAD/{folder}/SKILL.md",
               parse_json=False)
    if not raw:
        return None
    from inventory import read_frontmatter_text
    meta, _ = read_frontmatter_text(raw)
    return meta.get("description", "")


if __name__ == "__main__":
    from inventory import inventory
    items = check(inventory())
    for r in items:
        extra = f"  [pack of {len(r['catalog'])}]" if r.get("catalog") else ""
        print(f"{r['name']:28} {r['mechanism']:12} {r['update']}{extra}")
