# Private sync

Keep your global config **and** every project's private working notes (plans, kb,
findings) in a single private git repo — synced across machines and cloud sessions —
without ever committing any of it into the (often public) project repos.

This workflow is entirely opt-in — and it is **not part of dotagents**. `init` never
touches a project directory, and the two commands this model needs, `link-project` and
`sync-project`, are shipped by the `private-sync` overlay itself (commands *and* the
logic behind them). Installing the overlay is what makes them exist; a plain dotagents
has no private-sync surface at all.

## The idea

Your global config store **is** a private git repo. Its root is the per-user config;
a `projects/<name>/` tree inside it holds each project's private payload. For a
checked-out project, its `.agents` directory is a **symlink** into the store (the
project's `.gitignore` already excludes `.agents` per the Leakage rule, so the link
never lands in the public repo). `<name>` defaults to the project's basename, so a
local checkout and a cloud checkout of the same project resolve to the same store
entry.

## Commands

```bash
dotagents init                                            # base config
dotagents overlays add private-sync --source <overlays-checkout>  # commands + kb + hooks

# link-project / sync-project come FROM that overlay -- run `overlays add` first.
python -m dotagents link-project .   # symlink this project's .agents into its store
                                     #   (an existing real .agents/ is adopted on the
                                     #    first link; --copy mirrors it as a real dir
                                     #    for no-symlink systems)
python -m dotagents sync-project -m "msg"   # git pull --rebase / commit / push the store
python -m dotagents sync-project --remote <url> -m init   # one-command bootstrap
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

## Cloud sessions

The `private-sync` overlay installs SessionStart / Stop hooks that clone or pull the
private repo and link/sync the project each session — register them in your runner's
settings (a ready-made snippet ships with the overlay).

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
rewrite), clones/pulls the store, installs the CLI, and links the project — driven by
`AGENTS_REMOTE` / `DOTAGENTS_AGENTS_TOKEN` / `DOTAGENTS_CLI_INSTALL`
environment variables (the token is never committed). The full walkthrough ships with
the `private-sync` overlay.
