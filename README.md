# Skillview

One table for every skill, plugin, and tool installed for your coding agent — whatever installed it.

Capability arrives through unrelated mechanisms that don't know about each other: plugin marketplaces, `skills.sh`, package managers, and skills you wrote yourself. Each has its own storage location, its own metadata, and its own update command. Nothing shows them together, so tools get installed, forgotten, and left unused during the exact task they were installed for.

This shows you what you have, what each thing actually does for you, how to invoke it, and whether it's fallen behind upstream.

## Run it

```bash
python3 app.py
```

Then open <http://localhost:8477>.

No dependencies, no virtualenv, no build step — Python 3.8+ and the standard library. It binds to `127.0.0.1` only.

### Optional: plain-English descriptions

The table works immediately without this. To get descriptions that say what each tool does *for you* — rather than the keyword-stuffed blurb that exists to make a model trigger the skill — you need the `claude` CLI installed **and signed in**:

```bash
claude
```

Run that once in a terminal and complete the login prompt. Then click **Generate descriptions** in the dashboard.

This is genuinely optional. If the CLI is missing or not signed in, the dashboard says so and falls back to raw descriptions — nothing breaks. There's no API key to configure and no account to create beyond the Claude one you already have.

## What it shows

| Column | What it tells you |
|---|---|
| **Mechanism** | marketplace · skills.sh · local · unknown |
| **What it does for you** | plain-English, not the model-facing frontmatter blurb |
| **Author** | who wrote it — derived from whether an upstream source exists, never from a configured name |
| **Surface** | which agents actually have it installed |
| **Activation** | *auto* (fires itself) · *manual* (you type the command) · *needs starting* (something must be installed or running first) |
| **Update** | up to date · update available · no upstream · unknown |

Click **View** on any row for the full picture: every sub-command as a numbered one-liner, prerequisites with their exact commands, install path, source, and the command that updates it.

For plugins, "sub-commands" means everything the plugin ships — bundled skills, slash commands, agents, hooks, and MCP servers. A plugin carrying six skills shows six, not zero.

## The two buttons

**Check for updates** — one network call per distinct source repo, so a full pass is a few requests regardless of how many skills you have. `skills.sh` installs compare the recorded folder hash against the upstream git subtree SHA, which is exact. Marketplace plugins compare declared version to installed version.

**Generate descriptions** — shells out to the `claude` CLI already on your machine (no API key, no setup) and writes one plain sentence per tool into `descriptions.json`. That file is plain JSON: edit any line by hand and your version is kept. If the CLI isn't installed or isn't logged in, the table falls back to raw descriptions and says so.

## What it deliberately doesn't do

**It does not run updates.** It shows you the command. Executing updates means a local web server that shells out, which is a different security posture than one that only reads — that's the next version, behind its own review.

It also doesn't track usage, manage dependencies, or edit skills. It reads and reports.

## Honest limits

- Availability in the claude.ai **chat window** is server-side and leaves no local trace, so it isn't claimed. The Surface column reports only what's on disk.
- **Activation** for "needs starting" is detected by pattern-matching install hints in a skill's own docs. It catches the common shapes and will miss unusual phrasing.
- Install sources it doesn't recognise are marked *update manually* rather than guessed at. It never claims "up to date" without evidence.
- macOS and Linux. Windows is untested — patches welcome.

## Where it reads from

Nothing is hardcoded to a user or a path. Locations come from `$HOME` and `CLAUDE_CONFIG_DIR`.

```
~/.claude/plugins/installed_plugins.json   marketplace plugins
~/.claude/plugins/known_marketplaces.json  their source repos
~/.agents/.skill-lock.json                 skills.sh installs
~/.claude/skills/<name>/SKILL.md           everything else
```

Any of these being absent is normal — each is read independently, and a machine with none of them shows an empty table rather than an error.

## Layout

```
app.py            HTTP shim — routes to pure functions, nothing else
inventory.py      filesystem: read every mechanism into one row shape
upstream.py       network: update status per source repo
describe.py       subprocess: claude CLI → cached descriptions
ui.html           the whole frontend, vanilla, no CDN
test_inventory.py python3 test_inventory.py
```

`app.py` is intentionally thin so the web layer stays replaceable.

## Tests

```bash
python3 test_inventory.py
```

Fixture-driven, no framework. Covers the empty machine, each mechanism in isolation, an unrecognised installer, and a `SKILL.md` whose frontmatter real YAML parsers reject — that last one exists in the wild and takes down other tools.

## Licence

MIT.
