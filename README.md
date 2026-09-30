# dotagents

[![Test](https://img.shields.io/github/actions/workflow/status/jose-pr/dotagents/test.yml?branch=main&label=tests)](https://github.com/jose-pr/dotagents/actions/workflows/test.yml)
[![PyPI](https://img.shields.io/pypi/v/dotagents-cli.svg)](https://pypi.org/project/dotagents-cli/)
[![Documentation](https://img.shields.io/badge/docs-online-blue.svg)](https://jose-pr.github.io/dotagents/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://github.com/jose-pr/dotagents/blob/main/LICENSE)

Like dotfiles, but for AI coding agents: a portable, token-budgeted `~/.agents`
configuration that works across agent runners (Claude Code, Antigravity, Copilot,
Codex, pi, ...). dotagents is the **mechanism** — install a neutral base, then layer in
opt-in **overlays** that carry your standards (repo structure, CI/release discipline,
whatever workflows you want) — so you record them once instead of restating them every
session.

## Design

- **Core + load-on-demand routing.** `AGENTS.md` is the only always-loaded file: a
  handful of always-on rules plus a routing table. Task-specific detail lives in
  `flows/` and `kb/` files that an agent reads only when the task matches. You pay for
  what you use.
- **A neutral base + opt-in overlays.** `init` lays down a minimal, opinion-free
  **base overlay** (just the `AGENTS.md` managed block).
  Everything opinionated — workflows, language `kb/` files, repo templates, tools —
  lives in composable **overlays** you layer in explicitly
  (`dotagents overlays add <name>`), each contributing its own routing lines, rules,
  skills, and commands. Additive-only: overlays never overwrite something you've
  already customized.
- **Overlays carry the opinions, not the tool.** dotagents is the mechanism
  (install, compose, discover); *what* your agents should do is an overlay concern.
  The example overlays in this repo are a starting point — a planning/execution/review
  workflow set, language conventions, a release helper — but they're payloads riding on
  dotagents, swappable for your own. See the [docs](https://jose-pr.github.io/dotagents/)
  for what each ships.

## Layout

The config is a **base overlay** plus opt-in **overlays**; the `dotagents` CLI applies
them. Everything else is repo infrastructure.

| Path | What |
| --- | --- |
| `src/dotagents/` | The installable `dotagents` CLI (`init`/`overlays`/`context`/`env`/`build-pyz`/`about`, plus the bundled `findings`/`launch`) — that is the whole shipped surface; commands beyond it come from overlays or your own `dotagents/cmds/` modules |
| `src/dotagents/_overlay/` | The **base overlay**: the `AGENTS.md` block template `init` renders into the store (no `dotagents/` dir, no design log), the two bundled commands (`findings`, `launch`), and the hook scripts `init` deploys into an agent's config dir. Neutral — imposes no flows |
| `tools/` | Repo tooling, not shipped: `audit.py` (CI structure check), `pyz_smoke.sh` (the `.pyz` smoke test) and `cloud-setup.sh`. Personal scanning tools are not here either — keep them as command modules in your own private `.agents/dotagents/cmds/` |
| `install.py` | Thin shim over `dotagents.cli.main()` for a source checkout; installs the checkout's dependencies into the virtual environment running it (`--bootstrap` to allow another interpreter) |

The **example overlays** — `engineering` (the planning/execution/review workflow set,
its rules, repo templates and helper tools), the per-language `python` / `node` /
`rust`, `release`, `private-sync`, `net` and `recovery` — live on a separate
[`repo` branch](https://github.com/jose-pr/dotagents/tree/repo), not in `main`'s
tree: they are swappable payloads, not part of the tool. Name that branch as a repo
(`--repo`, see below) to install them. See the
[docs](https://jose-pr.github.io/dotagents/) for what each ships.

This repo's own working material (decisions, findings, plans) lives **privately** in
its untracked `.agents/` — like every project, `.agents/` is never tracked or pushed.

Each overlay's `<name>/overlay.toml` carries a `name`/`description`/`requires`/`routing`
manifest read by the `dotagents overlays` subcommand, which manages overlays by name
(`add`/`remove`/`list`/`sync`/`show`) — see [Managing overlays](#managing-overlays) below.

## Install

```bash
pip install dotagents-cli   # gives you `import dotagents` and the `dotagents` command
```

Or skip `pip` entirely with the self-contained downloadable `.pyz` (see below).

## Quick start

<!-- quickstart:begin -->
```bash
dotagents init -g   # the base config in ~/.agents, plus the Claude Code include and hooks
export AGENTS_OVERLAYS_REPO="https://github.com/jose-pr/dotagents.git@repo#overlays"
dotagents overlays add engineering python -g   # cloned with git, so git must be on PATH
dotagents overlays list -g
ls ~/.agents/overlays/engineering/flows/PLAN.md
```
<!-- quickstart:end -->

`AGENTS_OVERLAYS_REPO` names where overlays come from; set it in your shell profile, or
pass `--repo <spec>` to each `overlays` command instead. CI runs this block as written.

## `dotagents init`

`init` lays down the neutral base config. It writes the store's `AGENTS.md` managed
block, points Claude Code at it (an include in `~/.claude/CLAUDE.md` for the user
store, `<project>/.claude/CLAUDE.md` for a project), and wires each supporting agent's
hooks so `dotagents env` and `dotagents context` reach it at session start
(`--no-hooks` opts out) — but imposes no opinions (those come from `overlays add`). The
block is marker-delimited, so re-running `init` never clobbers what you've added around
it. **Scope**: the user store with `-g`/`--global` (`~/.agents`, or `$AGENTS_HOME`);
without it, the current directory's `.agents` — run it from the project root. When a
session has pinned `$AGENTS_PROJECT_ROOT` (or `$CLAUDE_PROJECT_DIR`) and you are inside
that directory, the pinned root is used; outside it, the nearest directory up with a
`.git` or its own `.agents`, with a warning.

```bash
dotagents init                          # project: ./.agents
dotagents init -g                       # user store: ~/.agents
dotagents init --bin-dir ~/.local/bin   # also write a `dotagents` command on PATH
dotagents init --dry-run                # show what would happen
dotagents init --force                  # replace AGENTS.md's content wholesale (backed up)
```

`--from <source>` selects another *base*: a base overlay directory, a dotagents
checkout (its `src/dotagents/_overlay`), a `file:`, `http(s):`, `zip:`, `sftp:` or `s3:`
URI (`pip install "dotagents-cli[uri]"` for all but `file:`), or a git repository
(`repo[@ref][#path]`). A remote one is fetched into `<user store>/.cache/overlays/`.
The bundled base ships inside the package, so `--from` is only for a base of your own.
`init` records it (without any credentials in the URL), and later `init` and
`overlays` runs compose over it.

`init` wires Claude Code plus any harness it is running inside (detected from its
environment); `--agents a,b` replaces that set, so include `claude` to keep it.

Overlays beyond the base are managed by name with `dotagents overlays add <name>` — it
installs into `<store>/overlays/<name>/` (discoverable) and publishes the overlay's
skills into the store's shared `skills/` dir. See below.

### Managing overlays

`dotagents overlays` manages opt-in overlays **by name**. `add` resolves each name
against **repos** in order — the first that offers the name wins. A repo is always a
**collection** of overlays: a directory (each subdirectory an overlay, e.g. a checkout of
the [`repo` branch](https://github.com/jose-pr/dotagents/tree/repo), whose overlays sit
under `overlays/<name>`) or a
JSON/TOML/YAML registry file mapping names to **sources**. It lives at a local path, an
`http(s)://` URL (a registry file, fetched), or inside a git repository named
`<repo>[@<branch|tag|commit>][#<path>]`, where the path picks the directory or registry
file in the checkout (no path: the checkout root). A source names **one** overlay: a
local directory, or a git spec whose path is the overlay's root directory (no path: the
repository root is the overlay); a relative path is relative to the registry file, and
inside a git checkout stays in the same repository at the same ref. Name repos with `--repo`,
`$AGENTS_OVERLAYS_REPO_<KEY>` / `$AGENTS_OVERLAYS_REPO`, or a `dotagents.{json,toml,yaml}`
in the project or user store.
Installed overlays are *discovered* by their presence under `<store>/overlays/`, and
each records the repo it was installed from.

```bash
dotagents overlays add python engineering  # install into the scope, publish skills, merge rules/routing
dotagents overlays list                    # installed (discovered) + available (from the repos)
dotagents overlays sync 'py*'              # refresh installed overlays matching a glob, resync their skills
dotagents overlays remove python           # delete the overlay dir + unpublish its skills
dotagents overlays show python             # manifest, requires, setup, skills, where it came from
```

Scope is **project** by default (the current directory's `.agents/`, resolved as for
`init`) or **user** with `-g`/`--global` (`~/.agents/`, the configurable store). Each
overlay installs as a directory (kept, discoverable), what its manifest `requires` is
installed first, its `routing`/`rules` merge into `AGENTS.md`'s managed block, and its
`skills/<name>/` are symlinked (or `--copy`'d, for Windows / no-symlink) into the
store's shared `skills/` so every agent sees the same skills.

- `add` and `sync` keep an installed overlay exactly as its source ships it: changed
  files are replaced and files the source does not ship are removed, anything edited
  there backed up under `<store>/install_backup/` first. What the `.gitignore` /
  `.ignore` at the overlay's root match (setup output, local state) and tool caches are left
  alone; `--prune` clears them too. A fresh `add` whose setup script fails is rolled
  back.
- `sync` refreshes each overlay from the repo it was installed from (`--repo` replaces
  that source), installs a `requires` added upstream, and re-merges the managed block.
- `remove` deletes the overlay's directory, unpublishes only the skills **it**
  published, and recomposes `AGENTS.md`'s managed block over the overlays that remain,
  so its rules and routing leave with it. It refuses an overlay another installed
  overlay requires, unless `--force`.
- `list` and `show` flag a `requires` no installed overlay provides; `show` names the
  repo an installed overlay came from.

**Overlay setup scripts.** An overlay may ship an **idempotent** `setup.py` at its root
(the recommended form: it runs under the same Python that runs dotagents, so it works on
every OS). After `add`/`sync` copies the overlay
in, dotagents runs the script automatically — so anything a human would otherwise
hand-follow (PATH/lib wiring, self-registration) is one script the tool runs, not a doc.
Presence of the script is the opt-in; skip it with `--no-setup`. The contract for
authors:

- **Idempotent** — safe to run on every `add`/`sync`; check-then-act, never blindly append.
- **cwd** is the installed overlay dir (`<store>/overlays/<name>/`), so reference
  your own files by relative path.
- **Env** carries `AGENTS_HOME` (the user store — never hardcode `~/.agents`),
  `AGENTS_SCOPE_ROOT` (the store the overlay is installed into: the user store with
  `-g`, the project's `.agents` otherwise), `AGENTS_SCOPE` (`user` or `project`) and
  `AGENTS_OVERLAY_DIR` (your own installed dir).
- A **non-zero exit fails the install** with a clear error (not a silent skip). For any
  outward or irreversible action the *script* must confirm first — the runner invokes a
  script you chose to install; it does not second-guess it.

### Downloadable `dotagents.pyz`

A self-contained zipapp with `duho`, `pathlib_next` and `tomli` bundled in (pure
Python, so one file runs on any OS and on Python 3.9+), so it needs no
`pip install`. Each release attaches one:
<https://github.com/jose-pr/dotagents/releases/latest/download/dotagents.pyz>.

```bash
python dotagents.pyz init -g --bin-dir ~/.local/bin      # lay down the base + a `dotagents` command
python -m dotagents build-pyz --out dist/dotagents.pyz   # or build it yourself (needs this repo checkout)
```

**Or let your agent do it:** point it at this repo and say —
> Read README.md, install `dotagents-cli` (into a virtual environment, or with `pipx`),
> run the Quick start block, and confirm
> `~/.agents/overlays/engineering/flows/PLAN.md` exists.

## Validate

```bash
python tools/audit.py --root .                  # validate THIS REPO's layout (CI tooling)
python tools/audit.py --check-templates --root .  # + template checks (needs 3.11+)
```

## Customize

Fork it — that's the point. Keep the base `AGENTS.md` small (the audit warns past
~2.5KB); put opinionated content in overlays. dotagents installs no design log: a
config miss is a **finding** — `dotagents findings add -g` for the user store
(`~/.agents/findings/`), no `-g` for a project (`<project>/.agents/findings/`) — and
triage closes each with its resolution, which is the record. How you keep any
decisions beyond that is up to you. A store's `dotagents/` directory exists only for
your own command modules (`dotagents/cmds/`), which you create when you add your first
one, and for `dotagents/config.toml`, which records an `init --from` base. This
repo follows the same rule: its working material lives in an **untracked** `.agents/`,
never committed — so what's public here is only the CLI, the base overlay, and the
opt-in overlays. If you fork, keep the tracked surface free of personal paths and
private project names. `tools/audit.py` validates this repo's *structure* only;
personal-leak scanning (machine paths, private plan names, session trailers) is a
separate, personal tool you run locally before a push — keep it as a command module in
your private `.agents/dotagents/cmds/`, not in the repo.

## Documentation

Full docs — install modes, the overlay model, the CLI command surface, private sync,
authoring your own overlays and commands, and the API reference — are at
[jose-pr.github.io/dotagents](https://jose-pr.github.io/dotagents/).

## License

MIT — see [LICENSE](LICENSE).
