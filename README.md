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

| Path | What |
| --- | --- |
| `AGENTS.md` | Always-loaded core: rules + routing (the payload's entry point) |
| `CLAUDE.md` | One-liner `@AGENTS.md` include for runners that want it |
| `flows/` | PLAN / EXEC / REVIEW / REPO task flows |
| `kb/` | Language directives (PYTHON, NODE, RUST) + RECOVERY playbook |
| `references/` | Repo file templates (README, manifests, CI workflows, ...) |
| `tools/` | `audit_config.py` (config integrity), `leak_check.py` (repo leak scan) |
| `install.py` | Copies the payload into `~/.agents` (backs up anything it replaces) |

## Install

```bash
python install.py            # into ~/.agents (backs up files it would change)
python install.py --dry-run  # show what would happen
```

Then wire your runner to it — e.g. Claude Code: put `@AGENTS.md` in
`~/.claude/CLAUDE.md`... which is exactly what the installed `CLAUDE.md` contains.

**Or let your agent do it:** point it at this repo and say —
> Read README.md and AGENTS.md, run `python install.py`, and confirm the final
> audit prints PASS.

## Validate

```bash
python tools/audit_config.py --root .        # validate this checkout
python tools/audit_config.py                 # validate the installed ~/.agents
python tools/audit_config.py --check-templates --root .   # needs Python 3.11+
```

## Customize

Fork it — that's the point. Keep `AGENTS.md` small (the audit warns past ~2.5KB);
push anything conditional into a `flows/` or `kb/` file and add a routing line.
Private, machine-specific work (plans, design logs) belongs in the gitignored
`.agents/` directory of this repo, never in the tracked payload — `tools/leak_check.py <repo>`
scans any repo's tracked files for that class of leak.

## License

MIT — see [LICENSE](LICENSE).
