# Overlays

The configuration is a **base overlay** plus opt-in **overlays**. The base is what
`init` lays down — the `AGENTS.md` managed block, with no opinions. Everything
opinionated ships as a named overlay you add explicitly.

## The overlay model

An overlay is a directory (optionally carrying an `overlay.toml` manifest) that
installs as a unit into `<store>/overlays/<name>/` — `~/.agents/overlays/<name>/` for
the user store, `<project>/.agents/overlays/<name>/` for a project. Its files keep
their relative paths inside that directory, and content refers to them through the
overlay's root variable (`$<NAME>_OVERLAY_ROOT/kb/X.md`, see
[below](#what-dotagents-env-wires-for-every-overlay)). `dotagents overlays` resolves
each overlay **by name** against the repos you name (`--repo`, `$AGENTS_OVERLAYS_REPO`,
a `dotagents.{json,toml,yaml}` in a store — see "Where overlays come from" below), and
installs it into the scope's overlays directory. Installed overlays are then
*discovered* by their presence there.

`overlays remove` deletes `<store>/overlays/<name>/` outright, so never author an
overlay inside that directory: keep your source elsewhere and install it from there.

Overlays are **additive**. `add` / `sync` never clobber a file you hand-edited inside
an installed overlay — a file that differs from the source is kept and reported.

## Overlay names

A name starts with an ASCII letter, continues with letters, digits, `_`, `.` or `-`,
and ends in a letter or digit. It is case-insensitive, and `_` and `.` count as `-`:
`My_Overlay`, `my.overlay` and `my-overlay` are one overlay, installed as
`overlays/my-overlay/`. The scope level names (`user`, `project`, `system`,
`project-root`, `default`, `overlay`) and Windows device names (`con`, `nul`, `com1`,
… with or without an extension) are refused.

## Example overlays

The example overlays below live on this repo's separate
[`repo` branch](https://github.com/jose-pr/dotagents/tree/repo), not in `main` —
they are **payloads riding on dotagents**, not part of the tool, so you swap them for
your own. Name the branch as a repo to install any of them:
`dotagents overlays add <name> --repo "https://github.com/jose-pr/dotagents.git@repo#overlays"`,
or `--repo <checkout>/overlays` for a local checkout of the branch.

| Overlay | What it carries |
| --- | --- |
| `engineering` | One opinionated engineering process. `rules/ENGINEERING.md` — always-on discipline merged into `AGENTS.md` (commit shape, release-tag consent, benchmark-as-evidence, token discipline, draft follow-ups). `flows/` — `PLAN.md` (a strong model writes precise, autonomous plans), `EXEC.md` (a cheaper model executes them without re-deriving context), `REVIEW.md` (file-threaded multi-agent plan review), `REPO.md` (the repo standard); `kb/MODELS.md` (executor/model selection). `references/` — language-neutral repo-file templates (README, CHANGELOG, LICENSE, `.gitignore`, docs-index, the plan template). `tools/` — `summarize_run` (command → verdict) and `compare_bench` (diff benchmark JSONs). The language overlays require it. |
| `python`, `node`, `rust` | Per-language `kb/` conventions + manifest templates + CI workflow templates. |
| `release` | A host-agnostic release helper: an agent-driven commit-plan loop plus tag/CI monitoring across GitHub (`gh`) and GitLab. |
| `private-sync` | The one-private-repo, per-project `.agents` model (`kb/PRIVATE_SYNC.md` + cloud hooks) — see [Private sync](private-sync.md). |
| `net` | Dependency-free HTTP tooling (a drop-in `curl` shim, an OS-trust-store `certifi` shim, an `httplib` session toolkit reading `AGENTS_PROXY`). |
| `recovery` | A config-recovery playbook for reconstructing a lost `~/.agents`. |

## Managing overlays

```bash
dotagents overlays add python engineering  # install into the scope, publish skills, merge rules/routing
dotagents overlays list                # installed (discovered) + available (from the repos)
dotagents overlays sync 'py*'          # refresh installed overlays matching a glob
dotagents overlays remove python       # delete the overlay dir, unpublish its skills, un-merge its rules
dotagents overlays show python         # describe one (manifest, requires, setup, skills, source)
```

Scope is **project** by default, or **user** with `-g` / `--global` (the configurable
store). Each overlay:

- installs as a directory (kept, discoverable), after what its manifest `requires`;
- records the repo it came from and the digest of every file it installed, in
  `<store>/overlays/<name>/.dotagents-install.json`;
- has its `routing` / `rules` merged into `AGENTS.md`'s managed block, which is
  recomposed from the pristine base over every installed overlay in priority order;
- has its skills published into the scope's shared skills dir, so every agent that
  reads that dir sees the same skills.

`sync` refreshes each overlay from the repo it was installed from (`--repo` replaces
that source for the run), removes files the source dropped that you never edited,
installs a `requires` added upstream, and reports files that differ from the source
instead of replacing them — `--overwrite` replaces them and `--prune` also removes
edited files the source dropped, each backed up under `<store>/install_backup/` first.

Removing an overlay deletes only its directory and unpublishes only the skills **it**
published (a copy the user edited is left alone). Its lines in `AGENTS.md`'s managed
block leave with it: the block is recomposed from the pristine base over the overlays
that remain. `remove` refuses an overlay another installed overlay requires, unless
`--force`; `list` and `show` flag a requirement no installed overlay provides.

## Where overlays come from: repos

`add`, `list` and `show` resolve a name against a list of **repos**, and for `add` the
first repo that offers the name wins. (`sync` goes back to the repo each overlay was
installed from.) A repo is **always a collection** of overlays, in one of two shapes:

- a **directory of overlays** — each subdirectory is an overlay, `<dir>/<name>/` (a
  checkout of the `repo` branch has them under `overlays/`, any folder);
- a **registry** — a JSON, TOML or YAML file mapping `<name-or-alias>` to the
  **source** of that one overlay (the whole document, or its `overlays` key). TOML
  needs Python 3.11+ or `tomli`, YAML needs `pyyaml`.

Both the repo and each source are written as a spec, `<location>[@<ref>][#<path>]`:
`<location>` is a local path, an `http(s)://` URL, or a git repository
(`https://…/x.git`, `git@host:org/x.git`, `ssh://…`, a local `…/x.git`; prefix `git+` to
force git); `<ref>` is a branch, tag or commit; `<path>` is inside it. The two differ
in what the path names:

- for a **repo**, the collection: a directory (of overlays) or a registry file in the
  checkout, the checkout root with no path — `--repo …/dotagents.git@repo#overlays`
  names the `overlays/` directory of the `repo` branch;
- for a **source** (a registry value), the overlay: the path is the overlay's root
  directory, and with no path **the repository root is the overlay** —
  `…/dotagents.git@repo#overlays/net` is the `net` overlay itself. Given as `--repo`,
  that form would list `net`'s own subdirectories as overlays.

A `scheme://` location that is not git is a **pathlib_next path**. With the `uri`
extra (`pip install 'dotagents-cli[uri]'`; `[http]`, `[sftp]`, `[s3]` add the schemes'
own clients) any scheme pathlib_next speaks works like a local path: an `http(s)://`
directory listing, an `sftp`, `s3`, `dav` or `github` tree is a directory of overlays
as a repo or one overlay as a source, a file is a registry; an archive is a tree too —
`zip:<archive-uri>!/<inner path>` (`tar:`, or `archive:` to auto-detect), the archive
itself local (`file:`) or at any URL: `zip:https://h/overlays.zip!/overlays`. Remote content is
materialized into `<user store>/.cache/overlays/uri/`, synced once per run, and used
from there; `file://` is a local path, no copy. Without the extra, an `http(s)://`
location can still be a registry file (fetched with the standard library), and
anything else says which extra it needs. The `.pyz` vendors none of this unless built
with `build-pyz --extras uri,http`.

A source written as a **relative path** (`./python`, `../shared/net`, `overlays/rust`)
is relative to the registry it is in: the registry file's directory for a local file,
and for a registry inside a git checkout the **same repository at the same ref**, with
the path joined onto the registry file's directory (`overlays/reg.toml` saying `./rust`
means `<repo>@<ref>#overlays/rust`), and for a registry at a URL the URL beside it
(`https://h/cfg/reg.json` saying `./rust` means `https://h/cfg/rust`).

Git checkouts are cached under `<user store>/.cache/overlays/`, one per repository and
ref, and fetched again once per run. That directory carries a `.gitignore` of `*`, so
a store kept in git never records them. Credentials in a repo URL never reach a log
line, a cache directory name or a checkout's `.git/config`, and an install record
keeps the URL without them (so `sync` of such an overlay needs the repo configured
again, or `--repo`).

The repos, in order:

1. `--repo <spec>` (repeatable);
2. `$AGENTS_OVERLAYS_REPO_<KEY>=<spec>`, sorted by `KEY`, then `$AGENTS_OVERLAYS_REPO`
   (the default repo);
3. `<project store>/dotagents.{json,toml,yaml,yml}`, then the user store's.

No build bundles overlays; with none of these configured, `add` fails with an error
that names them.

```toml
# ~/.agents/dotagents.toml
[overlays]
engineering = "https://github.com/you/dotagents.git@repo#overlays/engineering"
python      = "https://github.com/you/dotagents.git@repo#overlays/python"
mytool      = "git@github.com:you/mytool-overlay.git@v2"      # no path: the repo root is the overlay
local       = "~/src/overlays/local"                           # a local overlay directory
mine        = "./my-overlays/mine"                             # relative to this file: ~/.agents/my-overlays/mine
```

```bash
dotagents overlays add engineering -g                                        # from the registry above
dotagents overlays add net -g --repo https://github.com/you/dotagents.git@repo#overlays
dotagents overlays sync -g                                                   # refetches from each recorded repo
```

## What `dotagents env` wires for every overlay

Nothing in an overlay has to set up its own paths. For every installed overlay,
`dotagents env` prepends `bin/` to `PATH`, lists an existing `lib/` in
`AGENTS_PYTHONPATH` (which it puts on the `PYTHONPATH` of the `env.py` scripts it runs,
never on the session's), and exports **`$<NAME>_OVERLAY_ROOT`** — the overlay's
installed directory (`NAME` is the overlay's name upper-cased, `-` → `_`:
`private-sync` → `PRIVATE_SYNC_OVERLAY_ROOT`).
That variable is how overlay content refers to itself and to other overlays: a
routing line reads `$ENGINEERING_OVERLAY_ROOT/flows/PLAN.md`, never `~/.agents/flows/PLAN.md`
— overlays install under `overlays/<name>/`, and the store itself can live anywhere
(`$AGENTS_HOME`). `dotagents context` expands both forms, `$ENGINEERING_OVERLAY_ROOT` and
`<ENGINEERING_OVERLAY_ROOT>`, at assembly time for its stdout and JSON output; a file
written by `context --write-agent` keeps the variable.

An overlay that needs to export something of its own ships an `env.py` (or a plain
`env` file) at its root. `dotagents env` runs it as `env.py --level <overlay name>`
(plus `--global` when the project tiers are skipped) with the environment assembled
so far, and reads its changes from stdout as JSON — one object, or one object per line,
merged in order. Values must be strings, and `null` unsets a variable. The full
contract is under [Commands → env](commands.md#env).

## `overlay.toml`

The manifest is read by the `overlays` command and the context assembler, with
`tomllib` (Python 3.11+) or `tomli` when either is installed, and otherwise with a
small built-in reader that covers these keys. Keys:

| Key | Meaning |
| --- | --- |
| `name` | The overlay's canonical name. |
| `description` | One-line summary shown by `overlays list`. |
| `requires` | Other overlays this one depends on; `add` installs them first. |
| `routing` | Lines appended to the core's "Load on demand" routing table. |
| `rules` | Overlay-relative markdown paths whose rule bullets append to "Always-on rules". |
| `priority` | Merge order (lower sorts earlier; unprioritized default is 500). |

## Skills

An overlay may ship `skills/<skill-name>/` directories. Publishing symlinks each (or
copies, where symlinks are unavailable) into the scope's shared skills dir, so every
agent that reads that dir picks up the same skills. Removing the overlay unpublishes
only the skills it published, then sweeps any now-broken symlinks.

## Setup scripts

An overlay may ship an **idempotent** `setup.py` at its root — the recommended,
OS-agnostic form: it runs under the same Python that runs dotagents, so it works on
every platform. After `add` / `sync` copies the overlay
in, dotagents runs the script automatically. Reserve it for real install-time work:
`PATH`, `AGENTS_PYTHONPATH` and `$<NAME>_OVERLAY_ROOT` are `dotagents env`'s job
(above), and an overlay's own env vars belong in its `env.py`, not in a script that
writes one into the store — none of the example overlays ship a setup script any more.
Presence of a script is the opt-in; skip it with `--no-setup`. The author contract:

- **Idempotent** — safe on every `add` / `sync`; check-then-act, never blindly append.
- **cwd** is the installed overlay dir, so reference your own files by relative path.
- **Environment** carries `AGENTS_HOME` (the user store), `AGENTS_SCOPE_ROOT` (the
  store the overlay is installed into), `AGENTS_SCOPE` (`user` or `project`) and
  `AGENTS_OVERLAY_DIR` (the overlay's own installed dir) — never hardcode a home path.
- A **non-zero exit fails the install** with a clear error, not a silent skip, and a
  fresh `add` is rolled back. Any outward or irreversible action must be confirmed by
  the *script* itself.

See [Authoring an overlay](authoring.md) to build your own.
