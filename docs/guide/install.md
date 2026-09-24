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

**Scope**: project by default (`<cwd>/.agents`), or the user store with `-g`/`--global`
(`~/.agents`). `--dest` overrides explicitly. `--bin-dir` additionally writes a
`dotagents` wrapper command onto your PATH (meaningful when running from a built `.pyz`).

```bash
dotagents init                          # project: <cwd>/.agents
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
  `dotagents` wrapper into `bin/` when it runs from a `.pyz`.
- `dotagents.{json,toml,yaml,yml}` — an optional overlay-repo registry for
  `overlays add` (see [Overlays](overlays.md)); `.cache/overlays/` holds remote
  overlay sources materialized from one.
- `GEMINI.md`, `.cursorrules`, `.github/copilot-instructions.md` — the managed block
  rendered for that adapter, written only when `init` runs for it (`--agents`).
- `install_backup/<timestamp>/` — what `init --force` replaced.
- `dotagents/cmds/` — only if you create it: your own command modules (see
  [Authoring → Custom commands](authoring.md#custom-commands)).

## Wiring your agent runner

`init` wires Claude Code plus any harness it is running inside (detected from its
environment); `--agents a,b` replaces that set, so include `claude` to keep it. A
harness without an include mechanism gets the assembled context through a hook,
`dotagents launch`, or `dotagents context --write-agent`.

## Verify an install

```bash
dotagents init
test -f ~/.agents/AGENTS.md
python -m dotagents overlays add engineering -g
test -f ~/.agents/overlays/flows/flows/PLAN.md
```
