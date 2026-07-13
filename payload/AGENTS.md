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
- **Global-config misses**: if these global instructions contributed to a mistake,
  misread, or avoidable rework — or you have an improvement idea for them — record a
  short note under `~/.agents/dotagents/findings/` (create it if absent) and move on.
  This is private capture only: never edit the dotagents source repo, its design log,
  or the installed payload for this — a separate triage pass (explicitly requested)
  reconciles accumulated findings into the repo.
- **Executors may leave draft follow-ups**: adjacent work or improvements discovered
  mid-execution get a new top-level `Status: draft` plan (idea + rough scope + why) in
  the project's `.agents/plans/`, never expanded or executed in the same pass.

## Load on demand
Read the matching file BEFORE starting such a task; skip it otherwise. Never load
preemptively "just in case".
- Write or revise a plan → `~/.agents/flows/PLAN.md`
- Execute a plan → `~/.agents/flows/EXEC.md`
- Multi-architect plan review (as main or reviewer) → `~/.agents/flows/REVIEW.md`
- Create a repo, bring one to standard, or touch CI/release/docs-site →
  `~/.agents/flows/REPO.md`
- Python / Node-TS / Rust work → `~/.agents/kb/PYTHON.md` / `NODE.md` / `RUST.md`
- You are the Antigravity agent → `~/.agents/antigravity.md`
- Another explicitly named agent with a `~/.agents/<agent>.md` → read that file
- Asked to iterate on this global config → the `dotagents` source repo (its
  `.agents/plans/config_design_log.md` design log); edit there, then reinstall.
  Private scratch (findings, draft config plans not yet ready for the repo) lives
  in `~/.agents/dotagents/{findings,plans}/` instead — never sanitized, never
  auto-reconciled into the repo
- Recover lost agent config/files → `~/.agents/kb/RECOVERY.md`

Templates (README, CHANGELOG, LICENSE, .gitignore, manifests, CI workflows) live under
`~/.agents/references/`; flow/kb files link the specific ones.
