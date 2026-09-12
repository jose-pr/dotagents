# `dotagents/` — what dotagents itself keeps in this config

`dotagents init` laid this store down. Everything an agent session loads lives
one level up, in the store root; this directory holds what the tool owns.

## The store, one level up

- `AGENTS.md` — the root config, read by every agent session. Keep it lean:
  always-on rules plus a "load on demand" routing list. Its content between the
  `<!-- dotagents:begin -->` / `<!-- dotagents:end -->` markers is managed —
  re-running `init` refreshes only that block, and `overlays add` / `remove` /
  `sync` recompose it over the installed overlays' rules and routing — and
  anything you write outside the markers stays. The block's first line names
  this very file, so a session's "I read …" annotation points at the real
  location, wherever the store lives.
- `overlays/<name>/` — installed overlays (`dotagents overlays add <name>`),
  discovered by presence; `dotagents env` exports one `<NAME>_OVERLAY_ROOT` each.
- `findings/` — the findings queue (`dotagents findings add -g …` records a
  config miss instead of editing the config mid-task; `list -g` / `show -g` read
  it; `done -g <name> -r …` moves it to `findings/processed/`, never deletes).
- `skills/` — the shared skills dir overlays publish into.

The include a harness actually reads is written by `init` into that harness's
own config dir — for Claude Code `~/.claude/CLAUDE.md` (user scope) or
`<project>/.claude/CLAUDE.md` (project scope), as a managed block that skips
itself when you already wrote the line by hand. Harnesses without an include
mechanism get the assembled context through a hook (`init` wires it),
`dotagents launch`, or `dotagents context --write-agent`.

## This directory

- `AGENTS.md` is NOT here: the template `init` renders lives in the package.
- `DECISIONS.md` + `decisions/` — a design log for this config itself (why a
  rule exists, what changed and when): a lean index plus one file per decision.
  Not loaded in normal sessions.
- `cmds/` — your own commands: any `*.py` defining a `duho` command class here
  becomes a `dotagents <name>` subcommand (see `cmds/README.md`).
- `hooks/` — helper scripts the agent hooks `init` wires call into.

## Growing your own config

As you build out routines you want agents to follow repeatedly (planning,
execution, code review, repo standards, language-specific conventions), put
each in its own file under the store and add one routing line to `AGENTS.md`'s
"Load on demand" list — loaded only when the task matches, never preemptively.
That keeps every-session cost low regardless of how much topical detail
accumulates. How you group those files is up to you; the example overlays use
`flows/` and `kb/`, but nothing in `dotagents` requires that layout.
