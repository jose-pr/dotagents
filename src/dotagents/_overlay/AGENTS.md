<!-- dotagents:begin -->
# Agent Directives

Startup: annotate that you read `~/.agents/AGENTS.md`.

## Always-on rules
- **Git**: logical commits (feature+tests / docs+config / CI split apart, never one
  monolith), `type: desc` format (`feat:`, `fix:`, `docs:`, `chore:`).
- **Leakage**: never create `CLAUDE.md` or commit private agent config into a repo
  unless explicitly asked. Repo `.gitignore` must exclude `.agents/`, `CLAUDE*`,
  `.claude` — but NOT a repo's root `AGENTS.md`, which is a **committed, public**
  file (the library interface doc, below).
- **Permissions**: full read/write/create/delete on any `.agents/` directory (plans,
  notes, the working `AGENTS.md`) and any root `AGENTS.md` — never ask.
- **Two kinds of `AGENTS.md`** (do not conflate):
  - **`<project>/.agents/AGENTS.md`** — PRIVATE working knowledge for an agent working
    *on* this repo: architecture notes, gotchas, per-directory guidance. Repo-root's
    `.agents/AGENTS.md` for project-wide, a subfolder's for subtree-specific (deeper
    extends/overrides broader for its subtree). Agents write it; the user edits it too
    and their directives win on conflict. Keep it lean; topical detail goes in
    `<project>/.agents/{kb,flows,references}/` behind routing lines. Gitignored.
  - **`<project>/AGENTS.md`** (repo root) — a COMMITTED, package-shipped, agent-facing
    description of how to *use* the library: public API, accepted args, return/contract,
    and gotchas — header-file-style, so a consuming agent understands it without reading
    the source. Shape/standard in `flows/REPO.md`. (This global `~/.agents/AGENTS.md`
    is neither — it's the global directives.)
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
