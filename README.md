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
| `payload/dotagents/log.md` | Empty design-log template — every install gets its own, no repo required |
| `payload/flows/` | PLAN / EXEC / REVIEW / REPO task flows |
| `payload/kb/` | Language directives (PYTHON, NODE, RUST) + RECOVERY playbook |
| `payload/references/` | Repo file templates (README, manifests, CI workflows, ...) |
| `payload/tools/` | `audit_config.py` (config integrity), `leak_check.py` (repo leak scan) |
| `install.py` | Copies `payload/` into `~/.agents` (backs up anything it replaces) |
| `AGENTS.md` | Directives for working *on this repo* (not part of the payload) |
| `.agents/` | This config's own design log and plans — the "why" behind every rule |

## Install

```bash
python install.py            # into ~/.agents (backs up files it would change)
python install.py --dry-run  # show what would happen
```

Then wire your runner to it — e.g. Claude Code: put `@AGENTS.md` in
`~/.claude/CLAUDE.md`... which is exactly what the installed `CLAUDE.md` contains.

**Or let your agent do it:** point it at this repo and say —
> Read README.md and payload/AGENTS.md, run `python install.py`, and confirm the
> final audit prints PASS.

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
line. Two design logs, two audiences: `~/.agents/dotagents/log.md` (from
`payload/dotagents/log.md`) is *your* private, per-install log — installed empty,
edited directly, never distributed. This repo's own `.agents/` (design log + plans)
is the public, sanitized record of how *this* config evolved; if you fork, keep
yours equally free of personal paths and private project names (`--repo-hygiene`
checks mechanically), and use `payload/tools/leak_check.py <repo>` to scan any
*other* repo for agent-plan leakage before its releases.

## License

MIT — see [LICENSE](LICENSE).
