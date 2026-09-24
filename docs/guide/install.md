# Install

`dotagents init` lays down the neutral **base config**; a self-contained downloadable
`.pyz` needs no `pip install` at all. Opinionated content is added afterwards with
[`overlays add`](overlays.md).

## `dotagents init` — lay down the base config

`init` writes the store's `AGENTS.md` — a few always-on rules and an empty "Load on
demand" routing list — points Claude Code at it (an include in `~/.claude/CLAUDE.md` for
user scope, `<project>/.claude/CLAUDE.md` for project scope), and wires the session
hooks that hand `dotagents env` / `dotagents context` to the harnesses that have hooks
(`--no-hooks` opts out). It imposes no opinions (those come from overlays). That content
is a marker-delimited **managed block**, so re-running `init` never clobbers anything
you added around it.

**Scope**: project by default — the project you are in: `<project>/.agents`, where the project is a pinned
`$AGENTS_PROJECT_ROOT` while you are inside it, else the nearest directory up
with a `.git` or its own `.agents`, else the current directory; from `~` that is the
user store — or the user store with `-g`/`--global` (`~/.agents`). A command that writes
from outside a pinned root says which project it used. A project store's block is a minimal one — a Startup line and empty
"Always-on rules" / "Load on demand" sections for its overlays — because every session
already reads the user store's rules. `--dest` overrides explicitly. `--bin-dir` additionally writes a
`dotagents` wrapper command onto your PATH.

```bash
dotagents init                          # project: <project>/.agents
dotagents init -g                       # user store: ~/.agents
dotagents init --bin-dir ~/.local/bin   # also write a `dotagents` command on PATH
dotagents init --dry-run                # show what would happen
dotagents init --force                  # replace AGENTS.md wholesale (backed up)
```

`--from <path-or-uri>` selects the *base* source for a plain `pip install`
environment (a git-checkout dir, or a `file:` / `http(s):` / `zip:` / `sftp:` / `s3:`
URI via `pip install "dotagents-cli[uri]"`). `init`'s base ships inside the package, so it
needs no `--from`.

## Downloadable `dotagents.pyz`

A self-contained zipapp with `duho` / `pathlib_next` and the required tools bundled
in, so it needs no `pip install`:

```bash
python -m dotagents build-pyz --out dist/dotagents.pyz   # build it (needs a repo checkout)
python dist/dotagents.pyz init --bin-dir ~/.local/bin    # lay down base + command, offline
```

## What a store holds

- `AGENTS.md` — the root config every session reads. Keep it lean: always-on rules
  plus a routing list. The block between `<!-- dotagents:begin -->` and
  `<!-- dotagents:end -->` is managed — `init` refreshes it, and `overlays add` /
  `remove` / `sync` recompose it over the installed overlays' rules and routing — and
  anything outside the markers is yours.
- `overlays/<name>/` — installed overlays (`dotagents overlays add <name>`), discovered
  by presence; `dotagents env` exports one `<NAME>_OVERLAY_ROOT` each.
- `skills/` — the shared skills dir overlays publish into.
- `findings/` — the findings queue, created by the first `dotagents findings add`
  (`-g` for the user store). `done` moves a finding to `findings/processed/` with its
  resolution appended; nothing is deleted.
- `bin/`, `lib/` — put on `PATH` / `PYTHONPATH` by `dotagents env`, along with the
  store's own env files (see [Commands → env](commands.md#env)). `init` writes the
  `dotagents` wrappers into `bin/`: at the `.pyz` it runs from, or running
  `"<python>" -m dotagents` for a plain install.
- `dotagents.{json,toml,yaml,yml}` — an optional overlay-repo registry for
  `overlays add` (see [Overlays](overlays.md)); `.cache/overlays/` holds remote
  overlay sources materialized from one.
- `install_backup/<timestamp>/` — what `init --force` replaced.
- `dotagents/cmds/` — only if you create it: your own command modules (see
  [Authoring → Custom commands](authoring.md#custom-commands)).
- `dotagents/config.toml` — only after `init --from`: records that base, so a later
  `init` and every `overlays add` / `remove` / `sync` compose over it. Delete its
  `base` line to return to the bundled base.

## What runs, and from where

`dotagents env` and the command discovery run code from every store the session
walks: each store's `env.py` and env files, its `bin/` and `lib/` on `PATH` /
`PYTHONPATH`, its `dotagents/cmds/*.py`, and the same from its installed overlays. The
hooks `init` wires run them at session start.

- **The user store** is yours.
- **A project's `.agents/`** is trusted the way a harness trusts a repo's own
  `.claude/settings.json`: opening a session in a repository trusts it. Do not start
  sessions in repositories you do not trust.
- **The project root outside `.agents/`** contributes no code: a checkout's own
  `env.py`, `env`, `local.env` or `dotagents/cmds/` never runs. Keep your local
  overrides in `<project>/.agents/local.env`.
- **The system store** (`/etc/agents` on POSIX, or `$AGENTS_SYSTEM_ROOT`; no default on
  Windows) counts only when it exists and only administrators can write it; otherwise
  it is skipped with a warning.

## Wiring your agent runner

`init` wires Claude Code plus any harness it is running inside (detected from its
environment); `--agents a,b` replaces that set, so include `claude` to keep it (an
unknown name is an error). Claude Code gets an include of the store's `AGENTS.md`
(in `$CLAUDE_CONFIG_DIR` when that is set), Gemini CLI an `@` import in its
`GEMINI.md`, pi a pointer in its config dir. Codex and Antigravity keep their hooks in
a global config, so `init` wires them for the user store (`-g`) only. A harness
without an include mechanism gets the assembled context through a hook,
`dotagents launch`, or `dotagents context --write-agent`.

## Verify an install

```bash
dotagents init
test -f ~/.agents/AGENTS.md
python -m dotagents overlays add engineering -g
test -f ~/.agents/overlays/flows/flows/PLAN.md
```
