# Install

```bash
pip install dotagents-cli              # the `dotagents` command and `import dotagents`
pip install "dotagents-cli[uri]"       # + remote overlay sources beyond git and http(s) registries
```

The `http`, `sftp` and `s3` extras add those schemes' own clients to `uri`. With no
`pip` at all, use the [downloadable `dotagents.pyz`](#downloadable-dotagentspyz).

`dotagents init` then lays down the neutral **base config**. Opinionated content is
added afterwards with [`overlays add`](overlays.md).

## `dotagents init` — lay down the base config

`init` writes the store's `AGENTS.md` — a few always-on rules and an empty "Load on
demand" routing list — points Claude Code at it (an include in `~/.claude/CLAUDE.md` for
user scope, `<project>/.claude/CLAUDE.md` for project scope), and wires the session
hooks that hand `dotagents env` / `dotagents context` to the harnesses that have hooks
(`--no-hooks` opts out; see [Commands → Hook wiring](commands.md#hook-wiring)). It
imposes no opinions (those come from overlays). That content is a marker-delimited
**managed block**, so re-running `init` never clobbers anything you added around it.
A re-run also keeps the rules and routing of the overlays already installed.

**Scope**: the user store with `-g`/`--global` (`~/.agents`, or `$AGENTS_HOME`). Without
it, the project store `<root>/.agents`, where the root is:

1. a pinned `$AGENTS_PROJECT_ROOT` (then `$CLAUDE_PROJECT_DIR`) while the current
   directory is inside it;
2. with a pin, but from outside it: the nearest directory up from the current one with a
   `.git` or its own `.agents` (never your home directory or above), else the current
   directory — with a warning naming the directory used;
3. with no pin: the current directory. Run `init` from the project root.

When the project's `.agents` is the user store itself (run from `~`), the scope is the
user store. A project store's block
is a minimal one — a Startup line and empty "Always-on rules" / "Load on demand"
sections for its overlays — because every session already reads the user store's
rules. `--dest` overrides the resolved location. `--bin-dir` additionally writes a
`dotagents` wrapper command onto your PATH.

```bash
dotagents init                          # project: ./.agents
dotagents init -g                       # user store: ~/.agents
dotagents init --bin-dir ~/.local/bin   # also write a `dotagents` command on PATH
dotagents init --dry-run                # show what would happen
dotagents init --force                  # replace AGENTS.md's content wholesale (backed up)
```

`--from <path-or-uri>` selects another *base* (a directory, or a `file:` / `http(s):` /
`zip:` / `sftp:` / `s3:` URI via `pip install "dotagents-cli[uri]"`). The bundled base
ships inside the package, so you need it only for a base of your own. `init` records it
in `<store>/dotagents/config.toml`, and later `init` and `overlays` runs compose over it.

## Downloadable `dotagents.pyz`

A self-contained zipapp with `duho` and `pathlib_next` bundled in (plus whatever
`build-pyz --extras` added), so it needs no `pip install`. Each release attaches one:
<https://github.com/jose-pr/dotagents/releases/latest/download/dotagents.pyz>.

```bash
python dotagents.pyz init -g --bin-dir ~/.local/bin      # lay down base + a `dotagents` command
python -m dotagents build-pyz --out dist/dotagents.pyz   # or build it (needs a repo checkout)
```

## What a store holds

- `AGENTS.md` — the root config every session reads. Keep it lean: always-on rules
  plus a routing list. The block between `<!-- dotagents:begin -->` and
  `<!-- dotagents:end -->` is managed — `init` refreshes it, and `overlays add` /
  `remove` / `sync` recompose it over the installed overlays' rules and routing — and
  anything outside the markers is yours.
- `overlays/<name>/` — installed overlays (`dotagents overlays add <name>`), discovered
  by presence; `dotagents env` exports one `<NAME>_OVERLAY_ROOT` each. Each carries a
  `.dotagents-install.json` record of the repo it came from and the files installed.
- `skills/` — the shared skills dir overlays publish into.
- `findings/` — the findings queue, created by the first `dotagents findings add`
  (`-g` for the user store). `done` moves a finding to `findings/processed/` with its
  resolution appended; nothing is deleted.
- `bin/` — put on `PATH` by `dotagents env`. `init` writes the `dotagents` wrappers
  into it: at the `.pyz` it runs from, or running `"<python>" -m dotagents` for a plain
  install.
- `lib/` — Python modules for the store's `env.py`: `dotagents env` publishes every
  level's `lib/` in `AGENTS_PYTHONPATH` and puts them on the `PYTHONPATH` of the
  `env.py` scripts it runs, never on the session's (see
  [Commands → env](commands.md#env)).
- `env.py`, `env`, `pre.env.py`, `pre.env` — the store's env layers; a project store
  also reads `local.env` / `pre.local.env` (see [Commands → env](commands.md#env)).
- `dotagents.{json,toml,yaml,yml}` — an optional overlay-repo registry for
  `overlays add` (see [Overlays](overlays.md)). The user store's `.cache/overlays/`
  holds the git checkouts and remote sources the repos materialize (the directory carries a
  `.gitignore` of `*`, so a store kept in git never records them).
- `install_backup/<timestamp>/` — what `init --force` replaced, and what
  `overlays sync --overwrite` / `--prune` replaced or removed.
- `dotagents/cmds/` — only if you create it: your own command modules (see
  [Authoring → Custom commands](authoring.md#custom-commands)).
- `dotagents/config.toml` — only after `init --from`: records that base, so a later
  `init` and every `overlays add` / `remove` / `sync` compose over it. Delete its
  `base` line to return to the bundled base.

## What runs, and from where

`dotagents env` and the command discovery run code from every store the session
walks: each store's `env.py` and env files, its `dotagents/cmds/*.py`, and the same
from its installed overlays; `env` also puts each store's and overlay's `bin/` on
`PATH`. The hooks `init` wires run them at session start.

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
`GEMINI.md`, pi a pointer in its config dir (user store only). Codex and Antigravity
keep their hooks in a global config, so `init` wires them for the user store (`-g`)
only; Antigravity is never detected, so it needs `--agents antigravity`. A harness
without an include mechanism gets the assembled context through a hook,
`dotagents launch`, or `dotagents context --write-agent`. The hooks themselves are
described under [Commands → Hook wiring](commands.md#hook-wiring).

## Verify an install

Run the [Quick start](../index.md#quick-start) block: it ends by listing
`~/.agents/overlays/engineering/flows/PLAN.md`, which exists only when `init -g` and
`overlays add` both worked.
