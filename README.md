# dotagents

[![Test](https://img.shields.io/github/actions/workflow/status/jose-pr/dotagents/test.yml?branch=main&label=tests)](https://github.com/jose-pr/dotagents/actions/workflows/test.yml)
[![Documentation](https://img.shields.io/badge/docs-online-blue.svg)](https://jose-pr.github.io/dotagents/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://github.com/jose-pr/dotagents/blob/main/LICENSE)

Like dotfiles, but for AI coding agents: a portable, token-budgeted `~/.agents`
configuration that works across agent runners (Claude Code, Antigravity, Copilot,
Codex, ...). dotagents is the **mechanism** — install a neutral base, then layer in
opt-in **overlays** that carry your standards (repo structure, CI/release discipline,
whatever workflows you want) — so you record them once instead of restating them every
session.

## Design

- **Core + load-on-demand routing.** `AGENTS.md` is the only always-loaded file: a
  handful of always-on rules plus a routing table. Task-specific detail lives in
  `flows/` and `kb/` files that an agent reads only when the task matches. You pay for
  what you use.
- **A neutral base + opt-in overlays.** `init` lays down a minimal, opinion-free
  **base overlay** (just the `AGENTS.md` scaffolding + design-log convention).
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
| `src/dotagents/` | The installable `dotagents` CLI (`init`/`overlays`/`context`/`env`/`build-pyz`, plus the bundled command modules `link`/`sync`) |
| `src/dotagents/_overlay/` | The **base overlay** `init` writes: `AGENTS.md` scaffolding, `CLAUDE.md`, `dotagents/DECISIONS.md` (empty design-log index), and the bundled `dotagents/cmds/` command modules (`link`/`sync`). Neutral — imposes no flows |
| `tools/` | Required tooling (not an overlay): `cloud-setup.sh` (`leak-check` moved to the opt-in `leak-check` overlay, D84) |
| `install.py` | Thin shim over `dotagents.cli.main()`, kept at this filename for muscle memory |

The **example overlays** — the `flows` workflow set, per-language `kb/` + templates,
`references`, `release`, `private-sync`, `net`, `recovery`, `tools` — live on a separate
[`overlays` branch](https://github.com/jose-pr/dotagents/tree/overlays), not in `main`'s
tree: they are swappable payloads, not part of the tool. `dotagents overlays add <name>`
resolves them from there (or from any `--source`). See the
[docs](https://jose-pr.github.io/dotagents/) for what each ships.

Named-agent directives aren't a shipped overlay — a named agent (Claude, Antigravity,
…) just reads its own `~/.agents/<agent>.md` on top of the shared `AGENTS.md`, which the
base overlay's routing already states. This config's own design log lives **privately**
under its untracked `.agents/dotagents/` (`DECISIONS.md` + one file per decision) — like
every project, `.agents/` is never tracked or pushed.

Each overlay's `<name>/overlay.toml` carries a `name`/`description`/`requires`/`routing`
manifest read by the `dotagents overlays` subcommand, which manages overlays by name
(`add`/`remove`/`list`/`sync`) — see [Managing overlays](#managing-overlays) below.

## Install

**`dotagents init`** lays down the neutral base config; a self-contained downloadable
`.pyz` needs no `pip install` at all.

`init` writes the `.agents/` scaffolding — the `AGENTS.md` managed block, the per-agent
`<CLAUDE|ANTIGRAVITY|...>.md → @AGENTS.md` pattern, the design-log convention — but
imposes no opinions (those come from `overlays add`). Its `AGENTS.md`/`CLAUDE.md` are a
marker-delimited managed block, so re-running `init` never clobbers what you've added
around it. **Scope**: project by default (`<cwd>/.agents`), or the user store with
`-g`/`--global` (`~/.agents`).

```bash
dotagents init                          # project: <cwd>/.agents
dotagents init -g                       # user store: ~/.agents
dotagents init --bin-dir ~/.local/bin   # also write a `dotagents` command on PATH
dotagents init --dry-run                # show what would happen
dotagents init --force                  # replace AGENTS.md/CLAUDE.md wholesale (backed up)
```

`--from <path-or-uri>` selects the *base* source for a `pip install`-only environment
(a git checkout dir, `file:`, `http(s):`, `zip:`, `sftp:`, or `s3:` URI via
`pip install "dotagents-cli[uri]"`); `init`'s base ships inside the package, so it needs no
`--from`.

Overlays beyond the base are managed by name with `dotagents overlays add <name>` — it
installs into `<scope>/.agents/overlays/<name>/` (discoverable) and publishes the
overlay's skills into the shared skills dir. See below.

### Managing overlays

`dotagents overlays` manages opt-in overlays **by name**, resolving each name against a
source directory of overlays — point `--source <dir>` (or `$AGENTS_OVERLAYS_SRC`) at
one, e.g. a checkout of the [`overlays` branch](https://github.com/jose-pr/dotagents/tree/overlays)
where the example overlays live. Installed overlays are *discovered* by their presence
under `<scope>/.agents/overlays/` — there is no registry file.

```bash
dotagents overlays add python flows        # install into the scope, publish skills, merge D59 rules/routing
dotagents overlays list                    # installed (discovered) + available (from source)
dotagents overlays sync 'py*'              # refresh installed overlays matching a glob, resync their skills
dotagents overlays remove python           # delete the overlay dir + unpublish its skills
```

Scope is **project** by default (`<project>/.agents/`, when run inside one) or **user**
with `-g`/`--global` (`~/.agents/`, the configurable store). Each overlay installs as a
directory (kept, discoverable), its `routing`/`rules` merge additively into `AGENTS.md`'s
managed block, and its `skills/<name>/` are symlinked (or `--copy`'d, for Windows /
no-symlink) into the shared `<scope>/.agents/skills/` so every agent sees the same skills.
`add`/`sync` are additive and never clobber a file you hand-edited inside an installed
overlay. Removing an overlay deletes only its dir and unpublishes only the skills **it**
published; its lines in `AGENTS.md`'s managed block are not auto-pruned (a warning points
at the manual edit, or re-run `install`).

**Overlay setup scripts.** An overlay may ship an **idempotent** `setup.py` at its root
(the recommended form: it runs under the same Python that runs dotagents, so it works on
every OS — the bundled `net` overlay is the model). An extensionless `setup` (a POSIX
shell script) is still honored as a legacy fallback, but it is discouraged: a shell
script isn't portable to Windows without a shell. After `add`/`sync` copies the overlay
in, dotagents runs the script automatically — so anything a human would otherwise
hand-follow (PATH/lib wiring, self-registration) is one script the tool runs, not a doc.
When both are present, `setup.py` wins. Presence of a script is the opt-in; skip it with
`--no-setup`. The contract for authors:

- **Idempotent** — safe to run on every `add`/`sync`; check-then-act, never blindly append.
- **cwd** is the installed overlay dir (`<scope>/.agents/overlays/<name>/`), so reference
  your own files by relative path.
- **Env** carries `AGENTS_HOME` (the resolved store path — never hardcode
  `~/.agents`) and `AGENTS_OVERLAY_DIR` (your own installed dir).
- A **non-zero exit fails the install** with a clear error (not a silent skip). For any
  outward or irreversible action the *script* must confirm first — the runner invokes a
  script you chose to install; it does not second-guess it.

**Downloadable `dotagents.pyz`** — a self-contained zipapp with `duho`/
`pathlib_next` and the required `tools/` bundled in, so it needs no `pip install`:

```bash
python -m dotagents build-pyz --out dist/dotagents.pyz   # build it (needs this repo checkout)
python dist/dotagents.pyz init --bin-dir ~/.local/bin    # lay down the base + a `dotagents` command, offline
```

Then wire your runner to it — e.g. Claude Code: put `@AGENTS.md` in
`~/.claude/CLAUDE.md`... which is exactly what the installed `CLAUDE.md` contains.

**Or let your agent do it:** point it at this repo and say —
> Read README.md, run `python install.py init && python install.py overlays add flows -g`,
> and confirm `~/.agents/overlays/flows/flows/PLAN.md` exists.

## Private sync (per-user + per-project, one private repo)

Keep your global config **and** every project's private `.agents` (plans, kb, findings)
in a single private git repo — synced across machines and cloud sessions — without ever
committing any of it into the (often public) project repos.

The idea: your global `~/.agents` **is** a private git repo. Its root is the per-user
config; a `projects/<name>/` tree holds each project's private `.agents` payload. For a
checked-out project, `<project>/.agents` is a **symlink** to `~/.agents/projects/<name>`
(the project's `.gitignore` already excludes `.agents/` per the Leakage rule, so the
link never lands in the public repo). `<name>` defaults to the project's basename, so a
local `~/code/app` and a cloud `/home/user/app` resolve to the same store.

```bash
dotagents init                                       # base
dotagents overlays add private-sync --source <overlays-checkout> # kb + cloud hooks
dotagents link .        # symlink this project's .agents into the private repo
                        #   (an existing .agents/ is adopted in on the first link;
                        #    --copy mirrors it as a real dir for no-symlink systems)
dotagents sync -m msg   # git pull --rebase / commit / push the private repo
dotagents sync --remote git@github.com:<you>/.agents.git -m init   # one-command bootstrap
```

In cloud sessions, the installed `~/.agents/hooks/private-sync-{start,stop}.sh` clone/pull
the private repo and link/sync the project per session — register them in
`~/.claude/settings.json` (see `~/.agents/hooks/settings.snippet.json`). For a **fresh
container** (no `~/.agents` yet), point the web environment's **setup-script** field at the
self-contained bootstrap in this repo — it fetches the latest each start, so there's
nothing to re-paste:

```bash
curl -fsSL https://raw.githubusercontent.com/<you>/dotagents/main/tools/cloud-setup.sh -o /tmp/dg-cloud-setup.sh && sh /tmp/dg-cloud-setup.sh
```

Use `curl … -o file && sh file`, not `curl … | sh`: with a pipe the setup field's exit
code is `sh`'s (0 on empty stdin), so a failed fetch is silently logged as success; `&&`
propagates the curl failure instead.

It authenticates (bypassing a hosted-runner `github.com`→proxy git rewrite), clones/pulls
`~/.agents`, installs the CLI, and links the project — driven by `AGENTS_REMOTE`
/ `DOTAGENTS_AGENTS_TOKEN` / `DOTAGENTS_CLI_INSTALL` env vars (token never committed). Full
walkthrough: `~/.agents/kb/PRIVATE_SYNC.md`.

## Validate

```bash
python tools/audit.py --root .                  # validate THIS REPO's layout (CI tooling)
python tools/audit.py --check-templates --root .  # + template checks (needs 3.11+)
python tools/audit_config.py --repo-hygiene .          # no personal leftovers tracked
```

## Customize

Fork it — that's the point. Keep the base `AGENTS.md` small (the audit warns past
~2.5KB); put opinionated content in overlays. Your `~/.agents/dotagents/DECISIONS.md`
is *your* private, per-install design log (index + `decisions/` files) — installed
empty, edited directly, never distributed. This repo follows the same rule: its own
design log and all working material live in an **untracked** `.agents/dotagents/`, never
committed — so what's public here is only the CLI, the base overlay, and the opt-in
overlays. If you fork, keep the tracked surface free of personal paths and private
project names. `dotagents audit` validates config *structure* only; personal-leak
scanning (machine paths, private plan names, session trailers) is a separate,
personal `leak-check` tool you run locally before a push — it lives in your private
`.agents/`, not shipped in this repo.

## Documentation

Full docs — install modes, the overlay model, the CLI command surface, private sync,
authoring your own overlays and commands, and the API reference — are at
[jose-pr.github.io/dotagents](https://jose-pr.github.io/dotagents/).

## License

MIT — see [LICENSE](LICENSE).
