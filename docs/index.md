# dotagents

**dotagents** is dotfiles, but for AI coding agents: a portable, token-budgeted
`~/.agents` configuration that works across agent runners (Claude Code, Antigravity,
Copilot, Codex, …) and records your engineering standards once — repo structure,
CI/release discipline, planning/execution/review workflows — instead of restating
them every session.

The `dotagents` CLI installs and manages that configuration.

## The mental model

- **A core that is always loaded, and routing to everything else.** `AGENTS.md` is
  the one file every session reads: a handful of always-on rules plus a routing
  table. Task-specific detail lives in separate files an agent opens only when the
  task matches. You pay context for what you use, not for the whole config.
- **A neutral base overlay + opt-in overlays.** `dotagents init` lays down a minimal,
  opinion-free **base overlay** — just the `AGENTS.md` scaffolding and the design-log
  convention. Everything opinionated (planning/execution/review flows, per-language
  knowledge bases, repo templates, helper tools) lives in composable **overlays** you
  layer in explicitly with `dotagents overlays add <name>`. Overlays are additive:
  they never overwrite a file you have already customized.
- **Two scopes.** Config installs into a **user** store (`~/.agents`, configurable)
  or a **project** store (`<project>/.agents`). Overlays, skills, commands, and env
  files all resolve across the same scope precedence.
- **One private repo for everything private.** Your global config and each project's
  private working notes can live in a single private git repo, synced across machines
  and cloud sessions, without any of it landing in the (often public) project repos.
  See [Private sync](guide/private-sync.md).

## Quick start

```bash
# Lay down the neutral base config into ~/.agents (block-merges AGENTS.md/CLAUDE.md):
python install.py init

# Layer in opinionated overlays by name, into the user scope:
python install.py overlays add flows python -g

# See what's installed vs. available:
python install.py overlays list -g
```

Then wire your agent runner to it — for Claude Code, `~/.claude/CLAUDE.md` just needs
`@AGENTS.md`, which is exactly what the installed `CLAUDE.md` already contains.

Continue with the [Install](guide/install.md) guide, or jump to
[Commands](guide/commands.md) for the full CLI surface.

## Not on PyPI yet

dotagents is currently distributed from source (a checkout, or a self-contained
`dotagents.pyz` zipapp) — there is no `pip install dotagents` from PyPI yet. The
[Install](guide/install.md) guide covers all three modes.
