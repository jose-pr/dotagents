# dotagents

**dotagents** is dotfiles, but for AI coding agents: a portable, token-budgeted
`~/.agents` configuration that works across agent runners (Claude Code, Antigravity,
Copilot, Codex, …). dotagents is the **mechanism** — install a neutral base, then layer
in opt-in **overlays** that carry your standards (repo structure, CI/release discipline,
whatever workflows you want) — so you record them once instead of restating them every
session.

The `dotagents` CLI installs, composes, and manages that configuration; the overlays
carry the opinions.

## The mental model

- **A core that is always loaded, and routing to everything else.** `AGENTS.md` is
  the one file every session reads: a handful of always-on rules plus a routing
  table. Task-specific detail lives in separate files an agent opens only when the
  task matches. You pay context for what you use, not for the whole config.
- **A neutral base overlay + opt-in overlays.** `dotagents init` lays down a minimal,
  opinion-free **base overlay** — just the `AGENTS.md` scaffolding and the design-log
  convention. Everything opinionated (workflow sets, per-language knowledge bases, repo
  templates, helper tools) lives in composable **overlays** you layer in explicitly with
  `dotagents overlays add <name>`. The overlays in this repo are examples — payloads
  riding on dotagents, swappable for your own; see [Overlays](guide/overlays.md) for
  what each ships. Overlays are additive: they never overwrite a file you have already
  customized.
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
dotagents init

# Layer in opinionated overlays by name, into the user scope:
dotagents overlays add flows python -g

# See what's installed vs. available:
dotagents overlays list -g
```

Then wire your agent runner to it — for Claude Code, `~/.claude/CLAUDE.md` just needs
`@AGENTS.md`, which is exactly what the installed `CLAUDE.md` already contains.

Continue with the [Install](guide/install.md) guide, or jump to
[Commands](guide/commands.md) for the full CLI surface.

## Distribution

The PyPI distribution is named **`dotagents-cli`** (the import package stays `dotagents`
and the command stays `dotagents`, so `pip install dotagents-cli` gives you both). It is
**not published to PyPI yet** — for now dotagents is distributed from source (a checkout,
or a self-contained `dotagents.pyz` zipapp attached to each GitHub release). The
[Install](guide/install.md) guide covers every mode.
