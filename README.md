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
- **Language/agent content is opt-in.** The required install is language- and
  agent-agnostic. Python/Node/Rust `kb/` files, named-agent directives, and
  language-specific manifests/CI templates live in `examples/` and only land in
  `~/.agents` via `install.py --with-examples` — additive-only, so it never
  overwrites something you've already customized.
- **Architect/executor split.** `flows/PLAN.md` makes a strong model write precise,
  autonomous plans; `flows/EXEC.md` makes a cheaper model execute them without
  re-deriving context; `flows/REVIEW.md` runs file-threaded multi-agent plan review.
- **Every rule earned its place.** The flows encode failure modes actually hit in
  practice (executors improvising installs, skipped tests read as passing, plans
  leaking into public changelogs, a symlink wiping the config...).

## Layout

`payload/` is the product — it installs 1:1 into `~/.agents`. Everything outside it
is repo infrastructure (installer, CI, this repo's own working notes).

| Path | What |
| --- | --- |
| `payload/AGENTS.md` | Always-loaded core: rules + routing (the payload's entry point) |
| `payload/CLAUDE.md` | One-liner `@AGENTS.md` include for runners that want it |
| `payload/dotagents/DECISIONS.md` | Empty design-log index (+ `decisions/` dir) — every install gets its own, no repo required |
| `payload/flows/` | PLAN / EXEC / REVIEW / REPO task flows |
| `payload/kb/RECOVERY.md` | Config-recovery playbook (language-agnostic, so it's required) |
| `payload/references/` | Language-agnostic repo templates (README, CHANGELOG, LICENSE, .gitignore, plan-shape example) |
| `payload/tools/` | `audit_config.py` (config integrity), `leak_check.py` (repo leak scan) |
| `payload/examples/` | Opt-in only (`dotagents install --with-examples`): language `kb/` files, named-agent directives (`antigravity.md`), language-specific manifests + CI workflows |
| `src/dotagents/` | The installable `dotagents` CLI package (`init`/`install`/`build-pyz`/`audit`) and its own minimal neutral base overlay (`src/dotagents/_overlay/`) — distinct from `payload/`, see below |
| `install.py` | Thin shim over `dotagents.cli.main()`, kept at this filename for muscle memory |
| `AGENTS.md` | Directives for working *on this repo* (not part of the payload) |
| `.agents/` | This config's own design log and plans — the "why" behind every rule |

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

**`dotagents install`** — this repo's full opinionated payload (today's
behavior: PLAN/EXEC/REVIEW/REPO flows, language kb, templates, tools):

```bash
python install.py install --from payload           # from a repo checkout
python install.py install --dry-run
python install.py install --from payload --with-examples   # also copy language/agent examples (additive-only)
python install.py install --from payload --bin-dir ~/.local/bin  # also write a `dotagents` command
```

A `pip install`-only environment (no repo checkout) has no bundled full
payload — pass `--from <path-or-uri>` pointing at one (a git checkout dir,
`file:`, `http(s):`, `zip:`, `sftp:`, or `s3:` URI via `pip install
"dotagents[uri]"`). `dotagents init`'s bundled base overlay has no such
limitation — it ships inside the package.

**Downloadable `dotagents.pyz`** — a self-contained zipapp with `duho`/
`pathlib_next` vendored in and this repo's `payload/` bundled at build time, so
it needs no `pip install` to run:

```bash
python -m dotagents build-pyz --out dist/dotagents.pyz   # build it (needs this repo checkout)
python dist/dotagents.pyz install --bin-dir ~/.local/bin # install the bundled payload + a `dotagents` command, offline
```

Then wire your runner to it — e.g. Claude Code: put `@AGENTS.md` in
`~/.claude/CLAUDE.md`... which is exactly what the installed `CLAUDE.md` contains.

**Or let your agent do it:** point it at this repo and say —
> Read README.md and payload/AGENTS.md, run `python install.py install --from payload`,
> and confirm the final audit prints PASS.

## Validate

```bash
python payload/tools/audit_config.py --root payload   # validate this checkout
python payload/tools/audit_config.py                  # validate the installed ~/.agents
python payload/tools/audit_config.py --check-templates --root payload  # needs 3.11+
python payload/tools/audit_config.py --repo-hygiene . # no personal leftovers tracked
```

## Customize

Fork it — that's the point. Keep `payload/AGENTS.md` small (the audit warns past
~2.5KB); push anything conditional into a `flows/` or `kb/` file and add a routing
line. Two design logs, two audiences: `~/.agents/dotagents/DECISIONS.md` (from
`payload/dotagents/DECISIONS.md`) is *your* private, per-install log — installed empty,
edited directly, never distributed. This repo's own `.agents/` (design log + plans)
is the public, sanitized record of how *this* config evolved; if you fork, keep
yours equally free of personal paths and private project names (`--repo-hygiene`
checks mechanically), and use `payload/tools/leak_check.py <repo>` to scan any
*other* repo for agent-plan leakage before its releases.

## License

MIT — see [LICENSE](LICENSE).
