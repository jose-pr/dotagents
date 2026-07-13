# Agent Directives

Startup: annotate that you read `~/.agents/AGENTS.md`.

## Always-on rules
- **Git**: logical commits (feature+tests / docs+config / CI split apart, never one
  monolith), `type: desc` format (`feat:`, `fix:`, `docs:`, `chore:`).
- **Releases**: pushing a `v*` tag requires the user's explicit consent for *that*
  release, every time — publish is irreversible. `ci-*` tags are always safe to push.
- **Leakage**: never create `CLAUDE.md` or commit agent configs/references into a repo
  unless explicitly asked. Repo `.gitignore` must exclude `AGENTS.md`, `.agents/`,
  `CLAUDE*`, `.claude`.
- **Permissions**: full read/write/create/delete on any `.agents/plans/` directory
  and any `AGENTS.md` — never ask.
- **Local knowledge — two files, two owners**: agents record architecture notes and
  gotchas in the relevant directory's `AGENTS.md` (repo root for project-wide,
  subfolder for subtree-specific — deeper extends/overrides broader for its subtree,
  the same chain as this global file → project). `<project>/.agents/AGENTS.md` is
  **user-managed**: always read it, never write/create it unless explicitly told;
  on conflict it overrides agent-written `AGENTS.md` notes. Keep root `AGENTS.md`
  lean like this file — always-on facts plus a load-on-demand routing list; topical
  detail lives in `<project>/.agents/{kb,flows,references}/` (mirroring `~/.agents`),
  loaded only when a routing line matches the task.
- **Plans**: ALWAYS under the project the plan is about:
  `<project>/.agents/plans/<name>.md`, descriptive snake_case names; sub-plans at
  `.../<name>/<sub>.md`. Finished plans move to `<project>/.agents/plans/completed/`
  (preserve sub-tree). **`~/.agents/plans/` is never a plan home** — only harness
  plan-mode scratch lands there; re-home such files into the project (creating
  `.agents/plans/` if needed) and delete the scratch copy. Active-work scans read
  only the top level of the project's plans dir — never descend into `completed/`
  unless asked about history.
- **Global-config misses**: if these instructions caused a mistake, misread, or
  avoidable rework, or you have an improvement idea, drop a short note in
  `~/.agents/dotagents/findings/` and move on — don't edit the config itself. A
  requested triage pass later folds notes into `~/.agents/dotagents/log.md` and
  moves each file (never deletes) to `findings/processed/`.
- **Draft follow-ups**: adjacent work found mid-execution gets a new `Status: draft`
  plan (idea + scope + why) in the project's `.agents/plans/` — never expanded or
  executed in the same pass.

## Load on demand
Read the matching file BEFORE starting such a task; skip it otherwise. Never load
preemptively "just in case".
- Write or revise a plan → `~/.agents/flows/PLAN.md`
- Execute a plan → `~/.agents/flows/EXEC.md`
- Multi-architect plan review (as main or reviewer) → `~/.agents/flows/REVIEW.md`
- Create a repo, bring one to standard, or touch CI/release/docs-site →
  `~/.agents/flows/REPO.md`
- Language-specific work → `~/.agents/kb/<language>.md` if present (installed
  on request only; see `~/.agents/examples/kb/` for available languages)
- You are a named agent (e.g. Antigravity) with a `~/.agents/<agent>.md` → read it
  (installed on request only; see `~/.agents/examples/`)
- Asked to iterate on this global config → `~/.agents/dotagents/log.md` (design log)
  and `~/.agents/dotagents/{findings,plans}/` (private scratch). If a `dotagents`
  source checkout also exists, edit there and reinstall; otherwise edit `~/.agents/`
  directly

Generic templates (README, CHANGELOG, LICENSE, .gitignore, plan-shape example) live
under `~/.agents/references/`. Language manifests, per-language CI workflows, and
other agents' directives are opt-in examples under `~/.agents/examples/` — not
installed by default; ask before copying one in and never overwrite an existing file.
