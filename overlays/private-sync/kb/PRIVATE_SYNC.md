# Private agents sync — one private repo, per-user + per-project

Keep your global config **and** every project's private agent state in a single
private git repo, synced across machines and cloud sessions, without ever committing
any of it into the (often public) project repos.

## The model

Your global `~/.agents` **is** a private git repo. Its root is the per-user config
(`AGENTS.md`, `flows/`, `kb/`, the `dotagents/` design log). A new `projects/` tree
holds each project's private `.agents` payload:

```
~/.agents/                       # = clone of your private repo
├── AGENTS.md  CLAUDE.md          # per-user config (dotagents-managed block + your edits)
├── flows/  kb/  references/      # your global overlays
├── dotagents/                    # design log — private + synced too
└── projects/                     # per-project private .agents payloads
    ├── <project-a>/              # plans/  kb/  findings/  AGENTS.md (user-managed)
    └── <project-b>/ ...
```

For each checked-out project, `<project>/.agents` is a **symlink** →
`~/.agents/projects/<name>`. The project repo's own `.gitignore` already excludes
`.agents/` (the global Leakage rule), so the symlink is never committed to the project
— but everything behind it lives in, and syncs through, the one private repo.

`<name>` defaults to the project directory's basename, so a checkout at
`~/code/app` and a cloud checkout at `/home/user/app` both resolve to
`projects/app`. Override with `--name` when two different repos share a basename.

## Commands

- `dotagents link [PATH]` — symlink `PATH/.agents` (default: current dir) to its
  store. An existing real `.agents/` is **adopted** into an empty store on the first
  link (its content moves into the private repo, then the symlink replaces it), so you
  can run it in a project that already has `.agents/`. Re-running is idempotent.
  - `--copy` mirrors the store as a real directory instead of symlinking (Windows or
    any no-symlink environment; a symlink failure auto-falls back to this).
  - `--force` backs up a conflicting real `.agents/` (both project and store carry
    content) or replaces a stale symlink.
- `dotagents sync` — `git pull --rebase` / commit / push the private repo.
  - `--project PATH` first copies a **copy-mode** project's `.agents` back into its
    store (symlinked projects need no copy-back — their `.agents` *is* the store).
  - `--remote <url>` bootstraps a fresh repo (`git init` + set `origin`) in one command.
  - `--no-pull` / `--no-push` / `-m <msg>` / `--dry-run` as expected.

## First-time setup

```bash
# 1. Lay down the per-user base (and any overlays you want) into ~/.agents.
python install.py install --overlays overlays/flows --overlays overlays/private-sync
# 2. Make ~/.agents a git repo pointing at your private remote, and push.
dotagents sync --remote git@github.com:<you>/.agents.git -m "init: agents repo"
git -C ~/.agents push -u origin HEAD
# 3. In any project, link its .agents into the private repo, then sync.
cd ~/code/some-project
dotagents link .
dotagents sync -m "link some-project"
```

On a second machine: `git clone git@github.com:<you>/.agents.git ~/.agents`, then
`dotagents link .` inside each project you check out.

## Cloud sessions

Cloud containers start from a fresh clone with no `~/.agents`. Two hooks (installed by
this overlay to `~/.agents/hooks/`) make a cloud session identical to local:

- `private-sync-start.sh` (SessionStart): clones the private repo to `~/.agents` on
  first run (or `git pull --rebase` if present), then `dotagents link`s the project.
- `private-sync-stop.sh` (Stop): `dotagents sync --project` to push the session's
  changes back.

Wire them into your runner. For Claude Code, register them in **`~/.claude/settings.json`**
(NOT the project's `.claude/`, which is git-ignored) — see
`~/.agents/hooks/settings.snippet.json` for the exact block. On Claude Code on the web,
add the clone/link step to your environment's setup script instead, or commit a
repo `SessionStart` hook (see the `session-start-hook` skill).

Auth for the clone/push comes from the environment, never a committed file:

- `DOTAGENTS_AGENTS_REMOTE` — git URL of your private repo. Prefer a **tokenless** HTTPS
  URL (`https://github.com/<you>/.agents.git`) paired with `DOTAGENTS_AGENTS_TOKEN`.
- `DOTAGENTS_AGENTS_TOKEN` — **(recommended)** a fine-grained PAT scoped to just this
  repo (Contents: read/write). The hooks wire it via a git credential helper that reads
  it from the environment at auth time, so the secret is never written to `.git/config`
  or any file on disk. A token embedded directly in `DOTAGENTS_AGENTS_REMOTE` also works
  but is then persisted in `.git/config` — avoid it.
- `DOTAGENTS_AGENTS_DIR` — where the repo lives (default `$HOME/.agents`).

Why a PAT (not the session's own GitHub auth): a hosted agent runner authenticates git
through a per-session, repo-scoped credential, and a repo whose name starts with `.`
can't be added to that scope — so a dot-named private repo needs its own token. (A
non-dot repo name can instead use the runner's built-in auth with no token.) Store the
token as an environment secret and treat its read/write scope as sensitive; the cloud
network policy must allow `github.com`.

## Gotchas

- **`.gitignore` must exclude `.agents/`** in every project, or the symlink/copy can be
  committed to the (public) project. `dotagents link` warns when it's missing; the base
  overlay's Leakage rule already requires it.
- **Copy mode is two-way**: run `dotagents sync --project .` to push local edits back
  *before* re-running `dotagents link --copy` to pull global changes, or a refresh can
  overwrite unsynced local edits. Symlink mode has no such hazard — prefer it.
- **A `.agents.bak` dir** appears when `--force` backs up a conflicting real `.agents/`;
  reconcile and delete it.
- **The store is the source of truth** once linked: edit `<project>/.agents/...`
  transparently through the symlink; those writes land in `~/.agents/projects/<name>/`
  and sync with `dotagents sync`.
