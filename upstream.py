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

    if mech == "local":
        # Nothing upstream to be behind. Saying "up to date" here would imply a
        # guarantee that does not exist (R12).
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
        cmd = f"claude plugin marketplace update && claude plugin update {r['name']}"
        if declared is None or not declared:
            # Manifest reachable but versionless, or unreachable. Either way we
            # have no basis for a claim.
            return UNKNOWN, cmd, []
        return (UP_TO_DATE if declared == r["version"] else UPDATE), cmd, []

    # Unrecognised installer. Detected, and honest about not checking (Q8, C9).
    return MANUAL, "", []


def _catalog(tree, folder):
    """Sibling skills the pack ships — cached for v2 (R25), unused by v1 UI."""
    if not folder or "/" not in folder:
        return []
    parent = folder.rsplit("/", 1)[0]
    return sorted(
        p.rsplit("/", 1)[1] for p in tree["dirs"]
        if p.startswith(parent + "/") and p.count("/") == folder.count("/")
    )


if __name__ == "__main__":
    from inventory import inventory
    items = check(inventory())
    for r in items:
        extra = f"  [pack of {len(r['catalog'])}]" if r.get("catalog") else ""
        print(f"{r['name']:28} {r['mechanism']:12} {r['update']}{extra}")
