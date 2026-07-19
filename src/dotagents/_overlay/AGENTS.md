<!-- dotagents:begin -->
# Agent Directives

Startup: annotate that you read `~/.agents/AGENTS.md`.

## Always-on rules
- **Git**: logical commits (feature+tests / docs+config / CI split apart, never one
  monolith), `type: desc` format (`feat:`, `fix:`, `docs:`, `chore:`).
- **Leakage**: never create `CLAUDE.md` or commit agent configs/references into a repo
  unless explicitly asked. Repo `.gitignore` must exclude `AGENTS.md`, `.agents`
  (slashless — the link is a symlink, and a directory-only `.agents/` pattern does
  not match a symlink), `CLAUDE*`, `.claude`.
- **Permissions**: full read/write/create/delete on any `.agents/plans/` directory
  and any `AGENTS.md` — never ask.
- **Local knowledge — two files, two owners**: agents record architecture notes and
  gotchas in the relevant directory's `AGENTS.md` (repo root for project-wide,
  subfolder for subtree-specific — deeper extends/overrides broader for its subtree,
  the same chain as this global file → project). `<project>/.agents/AGENTS.md` is
  **user-managed**: always read it, never write/create it unless explicitly told;
  on conflict it overrides agent-written `AGENTS.md` notes. Keep root `AGENTS.md`
  lean like this file — always-on facts plus a load-on-demand routing list; topical
  detail lives in `<project>/.agents/{kb,flows,references}/`, loaded only when a
  routing line matches the task.
- **Plans**: ALWAYS under the project the plan is about:
  `<project>/.agents/plans/<name>.md`, descriptive snake_case names; sub-plans at
  `.../<name>/<sub>.md`. Finished plans move to `<project>/.agents/plans/completed/`
  (preserve sub-tree). **`~/.agents/plans/` is never a plan home** — only harness
  plan-mode scratch lands there; re-home such files into the project (creating
  `.agents/plans/` if needed) and delete the scratch copy.
- **Global-config misses**: if these instructions caused a mistake, misread, or
  avoidable rework, or you have an improvement idea, drop a short note in
  `~/.agents/dotagents/findings/` and move on — don't edit the config itself. A
  requested triage pass later folds notes into `~/.agents/dotagents/DECISIONS.md` and
  moves each file (never deletes) to `findings/processed/`.
- **Draft follow-ups**: adjacent work found mid-execution gets a new `Status: draft`
  plan (idea + scope + why) in the project's `.agents/plans/` — never expanded or
  executed in the same pass.

## Load on demand
Read the matching file BEFORE starting such a task; skip it otherwise. Never load
preemptively "just in case". This starter config ships with no flows installed yet —
grow this list as you add your own `~/.agents/flows/`, `~/.agents/kb/<language>.md`,
or named-agent `~/.agents/<agent>.md` files.
<!-- dotagents:end -->
