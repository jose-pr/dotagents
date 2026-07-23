# dotagents

Like dotfiles, but for AI coding agents: a portable, token-budgeted `~/.agents`
configuration that works across agent runners (Claude Code, Antigravity, Copilot,
Codex, ...) and records your engineering standards once — repo structure, CI/release
discipline, planning/execution/review workflows — instead of restating them every
session.

## Design

- **Core + load-on-demand routing.** `AGENTS.md` is the only always-loaded file: a
  handful of always-on rules plus a routing table. Task-specific detail lives in
  `flows/` and `kb/` files that an agent reads only when the task matches. You pay for
  what you use.
- **A neutral base + opt-in overlays.** `init` lays down a minimal, opinion-free
  **base overlay** (just the `AGENTS.md` scaffolding + design-log convention).
  Everything opinionated — planning/execution/review flows, language `kb/` files,
  repo templates, tools — lives in composable example **overlays** you layer in
  explicitly (`install --overlays <path>`). Additive-only: overlays never overwrite
  something you've already customized.
- **Architect/executor split.** `flows/PLAN.md` makes a strong model write precise,
  autonomous plans; `flows/EXEC.md` makes a cheaper model execute them without
  re-deriving context; `flows/REVIEW.md` runs file-threaded multi-agent plan review.
- **Every rule earned its place.** The flows encode failure modes actually hit in
  practice (executors improvising installs, skipped tests read as passing, plans
  leaking into public changelogs, a symlink wiping the config...).

## Layout

The config is a **base overlay** plus opt-in **overlays**; the `dotagents` CLI applies
them. Everything else is repo infrastructure.

| Path | What |
| --- | --- |
| `src/dotagents/` | The installable `dotagents` CLI (`init`/`install`/`link`/`sync`/`build-pyz`/`audit`) |
| `src/dotagents/_overlay/` | The **base overlay** `init` writes: `AGENTS.md` scaffolding, `CLAUDE.md`, `dotagents/DECISIONS.md` (empty design-log index). Neutral — imposes no flows |
| `overlays/flows/` | PLAN / EXEC / REVIEW / REPO task flows + `MODELS.md` (executor selection) |
| `overlays/recovery/` | `kb/RECOVERY.md` — config-recovery playbook |
| `overlays/references/` | Language-neutral repo templates (README, CHANGELOG, LICENSE, .gitignore, plan-shape example) |
| `overlays/python`, `node`, `rust` | Per-language `kb/` + manifests + CI workflows |
| `overlays/release/` | Host-agnostic release helper: agent-driven commit-plan loop + tag/CI monitoring across GitHub (`gh`) and GitLab (`gitlabq`) |
| `overlays/private-sync/` | `kb/PRIVATE_SYNC.md` + cloud `hooks/` for the one-private-repo, per-project `.agents` model (`dotagents link`/`sync`) |
| `overlays/tools/` | Helper tools you opt into: `summarize_run.py`, `compare_bench.py` |
| `tools/` | Required tooling (not an overlay): `audit_config.py`, `leak_check.py`, `cloud-setup.sh` |
| `install.py` | Thin shim over `dotagents.cli.main()`, kept at this filename for muscle memory |

Named-agent directives aren't a shipped overlay — a named agent (Claude, Antigravity,
…) just reads its own `~/.agents/<agent>.md` on top of the shared `AGENTS.md`, which the
base overlay's routing already states. This config's own design log lives **privately**
under its untracked `.agents/dotagents/` (`DECISIONS.md` + one file per decision) — like
every project, `.agents/` is never tracked or pushed.

Each `overlays/<name>/overlay.toml` carries a `name`/`description`/`requires`/`routing`
manifest for a future `dotagents overlays` subcommand; today overlays are applied by
path with `install --overlays`.

## Install

The `dotagents` CLI has two install modes, plus a self-contained downloadable
`.pyz` that needs no `pip install` at all.

**`dotagents init`** — a minimal, neutral starter. Explains the `.agents/`
hierarchy, the per-agent `<CLAUDE|ANTIGRAVITY|...>.md → @AGENTS.md` pattern, and
the `findings/` capture mechanism, but imposes none of this repo's own opinions
(no PLAN/EXEC/REVIEW flows, no model-routing). Its `AGENTS.md`/`CLAUDE.md` are
merged in as a marker-delimited managed block, so re-running `init` never
clobbers anything you've added around it:

```bash
python install.py init                 # writes ~/.agents/{AGENTS.md,CLAUDE.md,...}
python install.py init --dry-run        # show what would happen
python install.py init --force          # replace AGENTS.md/CLAUDE.md wholesale (backed up) instead of block-merging
```

**`dotagents install`** — the base overlay plus any overlays you opt into with
`--overlays <path>` (repeatable). The installer bundles only the base; overlays are
example directories you point at (this repo's `overlays/`, your own, or a URI):

```bash
python install.py install                                    # base only (like init)
python install.py install --overlays overlays/flows --overlays overlays/python
python install.py install --overlays overlays/flows --bin-dir ~/.local/bin  # also write a `dotagents` command
python install.py install --dry-run --overlays overlays/flows
```

Overlays are copied in additively (never clobbering an existing file), so re-running is
idempotent. `--from <path-or-uri>` selects the *base* source for a `pip install`-only
environment (a git checkout dir, `file:`, `http(s):`, `zip:`, `sftp:`, or `s3:` URI via
`pip install "dotagents[uri]"`); `init`'s base ships inside the package, so it needs no
`--from`. A future `dotagents overlays` subcommand will manage overlays by name.

**Downloadable `dotagents.pyz`** — a self-contained zipapp with `duho`/
`pathlib_next` and the required `tools/` bundled in, so it needs no `pip install`:

```bash
python -m dotagents build-pyz --out dist/dotagents.pyz   # build it (needs this repo checkout)
python dist/dotagents.pyz init --bin-dir ~/.local/bin    # lay down the base + a `dotagents` command, offline
```

Then wire your runner to it — e.g. Claude Code: put `@AGENTS.md` in
`~/.claude/CLAUDE.md`... which is exactly what the installed `CLAUDE.md` contains.

**Or let your agent do it:** point it at this repo and say —
> Read README.md, run `python install.py install --overlays overlays/flows`, and
> confirm `~/.agents/flows/PLAN.md` exists.

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
python install.py install --overlays overlays/private-sync   # kb + cloud hooks
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
`~/.agents`, installs the CLI, and links the project — driven by `DOTAGENTS_AGENTS_REMOTE`
/ `DOTAGENTS_AGENTS_TOKEN` / `DOTAGENTS_CLI_INSTALL` env vars (token never committed). Full
walkthrough: `~/.agents/kb/PRIVATE_SYNC.md`.

## Validate

```bash
python tools/audit_config.py --root .                  # validate this checkout
python tools/audit_config.py                           # validate the installed ~/.agents
python tools/audit_config.py --check-templates --root .  # needs 3.11+
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
project names (`--repo-hygiene` checks mechanically), and use `tools/leak_check.py
<repo>` to scan any *other* repo for agent-plan leakage before its releases.

## License

MIT — see [LICENSE](LICENSE).
