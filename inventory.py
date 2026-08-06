#!/usr/bin/env python3
"""Read every place agent capability gets installed, return one flat row list.

Four mechanisms, four storage formats, no shared schema:

    marketplace  ~/.claude/plugins/installed_plugins.json + the plugin cache
    skills.sh    ~/.agents/.skill-lock.json
    local        ~/.claude/skills/<name>/SKILL.md with no lockfile entry
    unknown      a bare version file dropped by some other installer

Every adapter is independently absent-tolerant: a machine with none of these
returns [], never raises. That is the whole first-run story for a stranger who
clones the repo, so it is tested rather than hoped for.

Pure functions only — no HTTP in scope. app.py is a shim over this.
"""
import json
import os
import re
from pathlib import Path

# Agent roots we can recognise. This is a map of *install locations*, not a list
# of known tools — adding an agent here never special-cases anyone's skills.
AGENT_ROOTS = {
    ".claude/skills": "Claude Code",
    ".agents/skills": "Shared (multi-agent)",
    ".gemini/skills": "Gemini CLI",
    ".opencode/skills": "OpenCode",
    ".codex/skills": "Codex",
}

# Lines that betray a prerequisite. Heuristic by design: skills have no declared
# field for "you must install this first", so this reads their prose. Catches the
# common shapes; replace with a real field if the format ever gains one.
PREREQ_RE = re.compile(
    r"((?:npm|pnpm|yarn)\s+install\s+-g\s+[^\s`\n]+"
    r"|npx\s+[^\s`\n]+\s+(?:start|install)"
    r"|(?:uv\s+tool\s+install|pipx\s+install|pip\s+install)\s+[^\s`\n]+"
    r"|brew\s+install\s+[^\s`\n]+)"
)
RUNNING_RE = re.compile(r"requires?\s+([A-Z][\w .-]{2,30}?)\s+to\s+be\s+(?:open|running)", re.I)


# ---------------------------------------------------------------- locations

def claude_dir():
    """Honour CLAUDE_CONFIG_DIR; fall back to ~/.claude. Never a literal path."""
    env = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(env).expanduser() if env else Path.home() / ".claude"


def agents_dir():
    return Path.home() / ".agents"


# ---------------------------------------------------------------- parsing

def read_frontmatter(path):
    """Same as read_frontmatter_text, for a file on disk."""
    try:
        return read_frontmatter_text(path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return {}, ""


def read_frontmatter_text(text):
    """Return the top `---` block as a flat dict, plus the body.

    Deliberately not a YAML parser. Real YAML chokes on descriptions containing
    a bare `: ` — one skill on this machine does exactly that and it takes down
    `npx skills list`. We only ever need scalar keys, so a line reader is both
    smaller and strictly more robust here.

    Text rather than path, because upstream SKILL.md files are read over the
    network for the pack catalog and never hit disk.
    """
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    meta = {}
    lines = text[3:end].splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        i += 1
        if not line.strip() or line.lstrip().startswith("#") or line[:1] in " \t":
            continue
        key, sep, val = line.partition(":")
        if not sep:
            continue
        val = val.strip()
        if val in (">", "|", ">-", "|-", ">+", "|+"):
            # Block scalar. The value is the indented run that follows — several
            # real skills write their description this way, and taking the
            # marker verbatim is how you end up rendering ">" as a description.
            folded, joiner = [], " " if val[0] == ">" else "\n"
            while i < len(lines) and (not lines[i].strip() or lines[i][:1] in " \t"):
                folded.append(lines[i].strip())
                i += 1
            val = joiner.join(f for f in folded if f)
        meta[key.strip()] = val.strip("\"'")
    return meta, text[end + 4:]


def _uniq(items):
    """Order-preserving dedupe — a README repeats its install line."""
    return list(dict.fromkeys(items))


def activation_of(meta, body):
    """auto | manual | needs starting, plus the command that starts it."""
    prereqs = _uniq(m.strip() for m in PREREQ_RE.findall(body or ""))
    needs_app = _uniq(m.strip() for m in RUNNING_RE.findall(body or ""))
    if prereqs or needs_app:
        return "needs starting", prereqs, needs_app
    manual = str(meta.get("disable-model-invocation", "")).lower() == "true"
    return ("manual" if manual else "auto"), [], []


def row(**kw):
    """One shape for every mechanism. Missing beats invented."""
    base = dict(
        name="", kind="skill", mechanism="", author="", is_local=False,
        surfaces=[], activation="auto", prereqs=[], needs_running=[],
        version="", install_path="", source_repo="", source_url="",
        source_path="", folder_hash="", installed_at="", description="",
        readme="", subcommands=[], parent="", purpose=[], size=0,
        # v2 keeps a slot here rather than reshaping the row later.
        usage=None,
    )
    base.update(kw)
    return base


def surfaces_for(name):
    """Which agent roots actually contain this skill.

    Availability in the claude.ai chat window is server-side and leaves no
    local trace, so it is not claimed here. Reporting only what is on disk.
    """
    out = []
    for rel, label in AGENT_ROOTS.items():
        if (Path.home() / rel / name).exists():
            out.append(label)
    return out


def describe_from(meta, fallback=""):
    return meta.get("description", "") or fallback


def _toml_description(path):
    """Just the description key. tomllib is 3.11+ and we support older."""
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            k, sep, v = line.partition("=")
            if sep and k.strip() == "description":
                return v.strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


# ---------------------------------------------------------------- adapters

def scan_plugins(cdir):
    """Marketplace plugins, expanded into everything they ship."""
    installed = _json(cdir / "plugins" / "installed_plugins.json").get("plugins", {})
    markets = _json(cdir / "plugins" / "known_marketplaces.json")
    rows = []
    for key, entries in installed.items():
        pname, _, mktname = key.partition("@")
        for e in entries or []:
            root = Path(e.get("installPath", ""))
            manifest = _json(root / ".claude-plugin" / "plugin.json")
            author = (manifest.get("author") or {})
            repo = ((markets.get(mktname) or {}).get("source") or {}).get("repo", "")
            subs = ship_manifest(root)
            rows.append(row(
                name=pname, kind="plugin", mechanism="marketplace",
                author=author.get("name", "") or mktname,
                version=e.get("version", ""), install_path=str(root),
                source_repo=repo,
                source_url=f"https://github.com/{repo}" if repo else "",
                installed_at=e.get("installedAt", ""),
                description=manifest.get("description", ""),
                readme=str(root / "README.md") if (root / "README.md").exists() else "",
                subcommands=subs,
                surfaces=["Claude Code"],
                activation="auto",
            ))
    return rows


def ship_manifest(root):
    """Everything a plugin ships — R23. Bundled skills are sub-commands too."""
    out = []
    for d in sorted((root / "skills").glob("*/SKILL.md")):
        meta, body = read_frontmatter(d)
        act, pre, run = activation_of(meta, body)
        out.append(dict(kind="skill", name=meta.get("name", d.parent.name),
                        invoke=f"/{meta.get('name', d.parent.name)}",
                        does=describe_from(meta), activation=act,
                        prereqs=pre, needs_running=run))
    for f in sorted((root / "commands").glob("*")):
        if f.suffix == ".md":
            meta, _ = read_frontmatter(f)
            does = describe_from(meta)
        elif f.suffix == ".toml":
            does = _toml_description(f)
        else:
            continue
        out.append(dict(kind="command", name=f.stem, invoke=f"/{f.stem}",
                        does=does, activation="manual", prereqs=[], needs_running=[]))
    for f in sorted((root / "agents").glob("*.md")):
        meta, _ = read_frontmatter(f)
        out.append(dict(kind="agent", name=meta.get("name", f.stem), invoke="",
                        does=describe_from(meta), activation="auto",
                        prereqs=[], needs_running=[]))
    if (root / "hooks").is_dir() or _json(root / ".claude-plugin" / "plugin.json").get("hooks"):
        out.append(dict(kind="hook", name="lifecycle hooks", invoke="",
                        does="Runs automatically on session events.",
                        activation="auto", prereqs=[], needs_running=[]))
    for d in sorted(root.glob("*-mcp")) + ([root / ".mcp.json"] if (root / ".mcp.json").exists() else []):
        out.append(dict(kind="mcp", name=d.name, invoke="",
                        does="MCP server shipped with this plugin.",
                        activation="auto", prereqs=[], needs_running=[]))
    return out


def scan_skills_sh(lock):
    """skills.sh installs. The lockfile is the only place authorship is recorded."""
    rows = []
    for name, v in (lock.get("skills") or {}).items():
        repo = v.get("source", "")
        path = _resolve_skill_dir(name)
        meta, body = read_frontmatter(path / "SKILL.md") if path else ({}, "")
        act, pre, run = activation_of(meta, body)
        rows.append(row(
            name=name, kind="skill", mechanism="skills.sh",
            author=repo.split("/")[0] if "/" in repo else repo,
            source_repo=repo, source_url=v.get("sourceUrl", ""),
            source_path=v.get("skillPath", ""), folder_hash=v.get("skillFolderHash", ""),
            installed_at=v.get("installedAt", ""), install_path=str(path or ""),
            description=describe_from(meta), surfaces=surfaces_for(name),
            activation=act, prereqs=pre, needs_running=run,
            readme=str(path / "SKILL.md") if path else "",
        ))
    return rows


def _resolve_skill_dir(name):
    for rel in AGENT_ROOTS:
        p = Path.home() / rel / name
        if (p / "SKILL.md").exists():
            return p
    return None


def scan_mcp(cdir):
    """MCP servers from user scope and from each known project's .mcp.json.

    Project paths come from Claude's own record of them, never from a hardcoded
    list, so this finds a stranger's projects as readily as ours.

    Auth state is not reported: for a remote server it lives behind the
    provider's OAuth flow and leaves nothing on disk. What *is* checkable is
    whether a stdio server's command still exists — a server pointing at a
    deleted virtualenv is dead weight, and that is the honest half of the
    question P2 asked.
    """
    rows, seen = [], set()
    top = _json(Path.home() / ".claude.json")

    sources = [("user", None, top.get("mcpServers") or {})]
    for proj, pv in (top.get("projects") or {}).items():
        sources.append(("project", proj, (pv or {}).get("mcpServers") or {}))
        sources.append(("project", proj, _json(Path(proj) / ".mcp.json").get("mcpServers") or {}))

    for scope, proj, servers in sources:
        for name, cfg in servers.items():
            key = (name, proj)
            if key in seen or not isinstance(cfg, dict):
                continue
            seen.add(key)
            cmd = cfg.get("command", "")
            url = cfg.get("url", "")
            transport = cfg.get("type") or ("stdio" if cmd else "remote" if url else "unknown")
            missing = bool(cmd) and not _resolves(cmd)
            where = Path(proj).name if proj else "all projects"
            rows.append(row(
                name=name, kind="mcp", mechanism="mcp",
                author="you" if scope == "project" else "",
                is_local=True,
                surfaces=[f"{where}"],
                activation="needs starting" if missing else "auto",
                prereqs=[f"missing: {cmd}"] if missing else [],
                install_path=str(Path(proj) / ".mcp.json") if proj else str(Path.home() / ".claude.json"),
                description=f"{transport} server — {_tilde(url or cmd)}".strip(),
            ))
    return rows


def _tilde(p):
    """Shorten a home path for display. Long absolute paths bury the point."""
    home = str(Path.home())
    return "~" + p[len(home):] if p.startswith(home) else p


def _resolves(cmd):
    """A command counts as present if it is a real path or found on PATH."""
    import shutil
    return Path(cmd).exists() or shutil.which(cmd) is not None


def dir_size(path):
    """Bytes on disk. Cheap enough to run inline — 19 trees measured in 0.11s."""
    if not path:
        return 0
    total = 0
    for root, _dirs, files in os.walk(path, onerror=lambda e: None):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def scan_local(cdir, claimed):
    """Anything in the skills dir with no upstream record — C3, R12.

    Authorship is *derived from the absence of a source*, never from matching a
    configured name. That is what makes the tool work for a stranger.
    """
    rows = []
    sdir = cdir / "skills"
    if not sdir.is_dir():
        return rows
    for d in sorted(sdir.iterdir()):
        if not (d / "SKILL.md").exists() or d.name in claimed:
            continue
        meta, body = read_frontmatter(d / "SKILL.md")
        act, pre, run = activation_of(meta, body)
        ver = next((f for f in d.glob(".*version")), None)
        mech, version = ("local", "")
        if ver:
            # Some other installer put this here. Detected, not identified —
            # so authorship is unknown, which is not the same as yours.
            mech = "unknown"
            version = ver.read_text(encoding="utf-8", errors="replace").strip()
        rows.append(row(
            name=meta.get("name", d.name), kind="skill", mechanism=mech,
            author="you" if mech == "local" else "",
            is_local=(mech == "local"), version=version,
            install_path=str(d), description=describe_from(meta),
            surfaces=surfaces_for(d.name) or ["Claude Code"],
            activation=act, prereqs=pre, needs_running=run,
            readme=str(d / "SKILL.md"),
        ))
    return rows


# ---------------------------------------------------------------- entry

def _json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def inventory():
    """Every mechanism, one list. Absent sources contribute nothing, silently."""
    cdir = claude_dir()
    lock = _json(agents_dir() / ".skill-lock.json")
    rows = scan_plugins(cdir)
    tracked = scan_skills_sh(lock)
    rows += tracked
    rows += scan_local(cdir, {r["name"] for r in tracked} | {r["name"] for r in rows})
    rows += scan_mcp(cdir)
    for r in rows:
        r["size"] = dir_size(r["install_path"]) if r["kind"] != "mcp" else 0
    rows.sort(key=lambda r: (r["mechanism"], r["name"].lower()))
    return rows


if __name__ == "__main__":
    import sys
    items = inventory()
    if "--json" in sys.argv:
        print(json.dumps(items, indent=2))
    else:
        for r in items:
            subs = f"  +{len(r['subcommands'])} shipped" if r["subcommands"] else ""
            print(f"{r['name']:28} {r['mechanism']:12} {r['activation']:14} "
                  f"{r['author'][:22]:24}{subs}")
        print(f"\n{len(items)} items")
