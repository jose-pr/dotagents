<!-- dotagents:begin -->
# Agent Directives

Startup: annotate that you read `~/.agents/AGENTS.md`.

## Always-on rules
- **Git**: logical commits (feature+tests / docs+config / CI split apart, never one
  monolith), `type: desc` format (`feat:`, `fix:`, `docs:`, `chore:`).
- **Leakage**: never create `CLAUDE.md` or commit private agent config into a repo
  unless explicitly asked. Repo `.gitignore` must exclude `.agents` (slashless — the
  link is a symlink, and a directory-only `.agents/` pattern does not match it),
  `CLAUDE*`, `.claude` — but NOT a repo's root `AGENTS.md`, which is a **committed,
  public** file (the library interface doc, below). Never print or echo `DOTAGENTS_*`
  values (no bare `env`/`printenv`); test emptiness instead — they hold secrets.
- **Permissions**: full read/write/create/delete on any `.agents/` directory (plans,
  notes, the working `AGENTS.md`) and any root `AGENTS.md` — never ask.
- **`AGENTS.md` has three roles, by location** (do not conflate). Only `.agents/`
  (+ `CLAUDE*`/`.claude`) is gitignored — every OTHER `AGENTS.md` is committed:
  - **`<project>/.agents/AGENTS.md`** — PRIVATE working knowledge for an agent working
    *on* this repo (architecture notes, gotchas, per-directory guidance). Repo-root or
    subtree `.agents/AGENTS.md`, deeper extends/overrides broader. Agents write it; the
    user edits it and wins on conflict. Keep lean; detail in
    `<project>/.agents/{kb,flows,references}/`. Gitignored.
  - **`<project>/AGENTS.md`** (repo root) — a COMMITTED, public **dev-facing overview**:
    what the project is, how the code is organized, entry points, how to build/develop/
    use it at a high level; points at the per-module headers below.
  - **`<project>/src/**/AGENTS.md`** — COMMITTED, package-shipped **"header" files**:
    one per source module/package dir, colocated with the code, describing THAT module's
    public API header-file-style (exports with signatures/accepted args/defaults/
    required, return-or-contract, env vars, gotchas) so a consuming agent uses it
    without reading the source. Kept current with the public API (same commit).
  (This global `~/.agents/AGENTS.md` is none of these — it's the global directives.)
  Shape/standards in `flows/REPO.md`.
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
