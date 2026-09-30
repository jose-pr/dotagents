# dotagents

**dotagents** is dotfiles, but for AI coding agents: a portable, token-budgeted
`~/.agents` configuration that works across agent runners (Claude Code, Antigravity,
Copilot, Codex, pi, …). dotagents is the **mechanism** — install a neutral base, then layer
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
  opinion-free **base overlay** — just the `AGENTS.md` managed block, whose rules
  include the findings queue for config misses. Everything opinionated (workflow
  sets, per-language knowledge bases, repo templates, helper tools) lives in
  composable **overlays** you layer in explicitly with `dotagents overlays add
  <name>`. The overlays in this repo are examples — payloads riding on dotagents,
  swappable for your own; see [Overlays](guide/overlays.md) for what each ships.
  An installed overlay is kept exactly as upstream ships it (what its `.gitignore` /
  `.ignore` protect stays yours), so your own changes live in your own files.
- **Two scopes.** Config installs into a **user** store (`~/.agents`, configurable)
  or a **project** store (`<project>/.agents`). Overlays, skills, commands, and env
  files all resolve across the same scope precedence.
- **One private repo for everything private.** Your global config and each project's
  private working notes can live in a single private git repo, synced across machines
  and cloud sessions, without any of it landing in the (often public) project repos.
  See [Private sync](guide/private-sync.md).

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

`init -g` writes the user store's `AGENTS.md` and wires your agent runner to it — for
Claude Code, an include of that file in `~/.claude/CLAUDE.md` plus the session hooks.
`AGENTS_OVERLAYS_REPO` names where overlays come from (here, the example overlays on
this repo's `repo` branch); set it in your shell profile, or pass `--repo <spec>` to
each `overlays` command instead.

Continue with the [Install](guide/install.md) guide, or jump to
[Commands](guide/commands.md) for the full CLI surface.

## Distribution

The PyPI distribution is named **`dotagents-cli`** (the import package stays `dotagents`
and the command stays `dotagents`):

```bash
pip install dotagents-cli            # the CLI and `import dotagents`
pip install "dotagents-cli[uri]"     # + non-git remote overlay sources and `init --from` URIs
```

The `http`, `sftp` and `s3` extras add those schemes' own clients. With no `pip` at
all, use the self-contained `dotagents.pyz` zipapp attached to each GitHub release.
The [Install](guide/install.md) guide covers every mode.
