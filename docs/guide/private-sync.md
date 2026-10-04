# Private sync

Keep your global config **and** every project's private working notes (plans, kb,
findings) in a single private git repo — synced across machines and cloud sessions —
without ever committing any of it into the (often public) project repos.

This workflow is entirely opt-in — and it is **not part of dotagents**. The two
commands this model needs, `link-project` and `sync-project`, are shipped by the
`private-sync` overlay itself (commands *and* the logic behind them). Installing the
overlay is what makes them exist; a plain dotagents has no private-sync surface at all.

## The idea

Your global config store **is** a private git repo. Its root is the per-user config;
a `projects/<name>/` tree inside it holds each project's private payload. For a
checked-out project, its `.agents` directory is a **symlink** into the store (the
project's `.gitignore` already excludes `.agents` per the Leakage rule, so the link
never lands in the public repo). `<name>` defaults to the project's basename, so a
local checkout and a cloud checkout of the same project resolve to the same store
entry.

## Commands

The overlay goes into the user store, since the store is what it syncs:

```bash
dotagents init -g                                             # base config in ~/.agents
dotagents overlays add private-sync -g \
  --repo "https://github.com/jose-pr/dotagents.git@repo#overlays"   # commands + kb + hooks

# link-project / sync-project come FROM that overlay -- run `overlays add` first.
dotagents link-project .   # symlink this project's .agents into its store
                           #   (an existing real .agents/ is adopted on the
                           #    first link; --copy mirrors it as a real dir
                           #    for no-symlink systems)
dotagents sync-project -m "msg"   # git pull --rebase / commit / push the store
dotagents sync-project --remote <url> -m init   # one-command bootstrap
```

Where the store lives and how it reaches other machines are **conventions, not
requirements**: the default store location is configurable, and a store that never
leaves the machine is a perfectly valid setup.

## Safety behaviors

- A project whose `.agents` is **itself a git checkout** is never adopted or copied
  back — `link-project` / `sync-project` skip it (with a message) so a nested repo is
  never swallowed. `link-project --force` backs the checkout up (git state intact) and
  links the store instead.
- **Copy mode** (`--copy`) makes `.agents` a real directory rather than a symlink;
  `sync-project` copies edits back into the store.
- **Default ignore rules.** A project's `.agents/scratch/` lives inside the store, so
  the built-in git path would commit it. The first `sync-project` on that path
  appends a block to the store's `.gitignore` that leaves out `scratch/`, `tmp/`,
  `__pycache__/`, hidden files (`.*`, keeping `.gitignore`, `.gitattributes`,
  `.gitkeep`, `.ignore` and `.dotagents-install.json`) and the machine-local
  overrides (`*.local.*`, `local.env`). It is written once, under a marker line:
  edit the block freely and keep that line. The overlay's `kb/PRIVATE_SYNC.md` has
  the block in full.

## Cloud sessions

The `private-sync` overlay ships SessionStart / Stop hook scripts that clone or pull
the private repo and link/sync the project each session. `overlays add` does not
register them: add them to your runner's settings yourself (for Claude Code,
`~/.claude/settings.json`), using the snippet the overlay ships in
`$PRIVATE_SYNC_OVERLAY_ROOT/hooks/settings.snippet.json`. The bootstrap below does that
merge for you.

For a **fresh container** with no config yet, point the web environment's setup-script
field at the self-contained bootstrap shipped in this repo — it fetches the latest each
start, so there is nothing to re-paste:

```bash
curl -fsSL https://raw.githubusercontent.com/<you>/dotagents/main/tools/cloud-setup.sh \
  -o /tmp/dg-cloud-setup.sh && sh /tmp/dg-cloud-setup.sh
```

!!! note
    Use `curl … -o file && sh file`, not `curl … | sh`: with a pipe the setup field's
    exit code is `sh`'s (0 on empty stdin), so a failed fetch is silently logged as
    success. `&&` propagates the curl failure instead.

The bootstrap authenticates (bypassing a hosted-runner `github.com` → proxy git
rewrite), clones/pulls the store, installs the CLI, links the project, and wires the
hooks into `~/.claude/settings.json` — driven by `AGENTS_REMOTE` /
`DOTAGENTS_AGENTS_TOKEN` / `DOTAGENTS_CLI_INSTALL` environment variables (the token is
never committed). `AGENTS_OVERLAYS_REMOTE` / `AGENTS_OVERLAYS_REF` choose where it
fetches the `private-sync` overlay from (default: this repo's `repo` branch); the old
`DOTAGENTS_OVERLAYS_REMOTE` / `DOTAGENTS_OVERLAYS_REF` names are still read when the
new ones are unset. The full walkthrough ships with the `private-sync` overlay
(`$PRIVATE_SYNC_OVERLAY_ROOT/kb/PRIVATE_SYNC.md`).
