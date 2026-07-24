# Install

`dotagents init` lays down the neutral **base config**; a self-contained downloadable
`.pyz` needs no `pip install` at all. Opinionated content is added afterwards with
[`overlays add`](overlays.md).

## `dotagents init` — lay down the base config

`init` writes the `.agents/` scaffolding: the `AGENTS.md` managed block, the per-agent
`<CLAUDE|ANTIGRAVITY|…>.md → @AGENTS.md` pattern, and the design-log/findings
convention — imposing no opinions (those come from overlays). Its `AGENTS.md`/`CLAUDE.md`
are a marker-delimited **managed block**, so re-running `init` never clobbers anything
you added around it.

**Scope**: project by default (`<cwd>/.agents`), or the user store with `-g`/`--global`
(`~/.agents`). `--dest` overrides explicitly. `--bin-dir` additionally writes a
`dotagents` wrapper command onto your PATH (meaningful when running from a built `.pyz`).

```bash
dotagents init                          # project: <cwd>/.agents
dotagents init -g                       # user store: ~/.agents
dotagents init --bin-dir ~/.local/bin   # also write a `dotagents` command on PATH
dotagents init --dry-run                # show what would happen
dotagents init --force                  # replace AGENTS.md/CLAUDE.md wholesale (backed up)
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

## Wiring your agent runner

After install, point your runner at the config. For Claude Code, put `@AGENTS.md` in
`~/.claude/CLAUDE.md` — which is exactly what the installed `CLAUDE.md` contains.

## Verify an install

```bash
dotagents init
test -f ~/.agents/AGENTS.md
python -m dotagents overlays add flows -g
test -f ~/.agents/overlays/flows/flows/PLAN.md
```
