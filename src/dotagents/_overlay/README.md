# dotagents skeleton — understanding the hierarchy

This is the minimal starter installed by `dotagents init`. It gives you the
scaffolding to build your own agent config from scratch — it intentionally carries
no opinionated flows, model-routing, or repo-standards content.

## Layout

- `AGENTS.md` — the root config, read by every agent session. Keep it lean: always-
  on rules plus a "load on demand" routing list. Its content between the
  `<!-- dotagents:begin -->` / `<!-- dotagents:end -->` markers is managed by
  `dotagents init` — re-running `init` refreshes only that block, never touching
  anything you add outside it.
- `CLAUDE.md` (and any other per-agent entry file, e.g. `ANTIGRAVITY.md`) — a
  one-liner (`@AGENTS.md`) that points that agent runner at the shared root config.
  Add one per agent runner you use; they all converge on the same `AGENTS.md`.
- `dotagents/DECISIONS.md` + `dotagents/decisions/` — a design log for this config
  itself (why a rule exists, what changed and when), as a lean index + one file per
  decision. Not loaded in normal sessions.
- `dotagents/findings/` — where agents drop short notes when the config caused a
  mistake or gap, instead of editing the config mid-task. `dotagents/findings/
  processed/` is where triaged notes land (moved, never deleted) once folded into
  `dotagents/DECISIONS.md`.

## Growing your own flows

As you build out routines you want agents to follow repeatedly (planning, execution,
code review, repo standards, language-specific conventions), add them under
`~/.agents/flows/`, `~/.agents/kb/<language>.md`, or `~/.agents/<agent>.md`, and add
one routing line to `AGENTS.md`'s "Load on demand" list per file — loaded only when
the task matches, never preemptively. This keeps every-session cost low regardless
of how much topical detail accumulates.

If you'd rather start from a fuller, opinionated example instead of building from
scratch, see this project's `examples/` payload (language knowledge bases, named-
agent directives, CI/reference templates) — copy pieces in deliberately, never
wholesale.
