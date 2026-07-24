# Install

The `dotagents` CLI has two install modes, plus a self-contained downloadable
`.pyz` that needs no `pip install` at all. All three lay down the same neutral
**base overlay**; opinionated content is added afterwards with
[`overlays add`](overlays.md).

## `dotagents init` — the minimal, neutral starter

`init` explains the `.agents/` hierarchy, the per-agent
`<CLAUDE|ANTIGRAVITY|…>.md → @AGENTS.md` pattern, and the findings-capture
mechanism, but imposes none of this project's own opinions (no planning/execution/
review flows, no model-routing). Its `AGENTS.md`/`CLAUDE.md` are merged in as a
marker-delimited **managed block**, so re-running `init` never clobbers anything you
added around it.

```bash
python install.py init                 # writes ~/.agents/{AGENTS.md,CLAUDE.md,…}
python install.py init --dry-run       # show what would happen
python install.py init --force         # replace AGENTS.md/CLAUDE.md wholesale (backed up)
                                        #   instead of block-merging
```

## `dotagents install` — base overlay plus wrapper scripts

`install` lays down the base overlay (like `init`), and can additionally install a
`dotagents` wrapper command onto your PATH.

```bash
python install.py install                             # base only (like init)
python install.py install --bin-dir ~/.local/bin      # base + a `dotagents` command
python install.py install --dry-run
```

`--from <path-or-uri>` selects the *base* source for a plain `pip install`
environment (a git-checkout dir, or a `file:` / `http(s):` / `zip:` / `sftp:` / `s3:`
URI via `pip install "dotagents[uri]"`). `init`'s base ships inside the package, so it
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
python install.py init
test -f ~/.agents/AGENTS.md
python -m dotagents overlays add flows -g
test -f ~/.agents/overlays/flows/flows/PLAN.md
```
