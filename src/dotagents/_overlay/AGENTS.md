<!-- dotagents:begin -->
# Agent Directives

Startup: annotate that you read `~/.agents/AGENTS.md`.

## Always-on rules
- **Git**: logical commits (feature+tests / docs+config / CI split apart, never one
  monolith), `type: desc` format (`feat:`, `fix:`, `docs:`, `chore:`).
- **Leakage**: never create `CLAUDE.md` or commit private agent config unless asked.
  Repo `.gitignore` excludes `.agents` (slashless — the link is a symlink, which a
  directory-only `.agents/` won't match), `CLAUDE*`, `.claude`. Never print `DOTAGENTS_*`
  values (no bare `env`/`printenv`) — they hold secrets; test emptiness instead.
- **Permissions**: full read/write/create/delete on any `.agents/` directory and any
  `src/**/AGENTS.md` — never ask.
- **`AGENTS.md`, two kinds** — no repo-root one:
  - **`<project>/src/**/AGENTS.md`** — COMMITTED, package-shipped "header" per module
    dir: that module's public API header-file-style (exports with signatures/args/
    defaults, return-or-contract, env vars, gotchas) so a consuming agent skips the
    source. Current with the API, same commit.
  - **`<project>/.agents/AGENTS.md`** — PRIVATE working knowledge (architecture,
    gotchas, per-dir guidance); deeper subtree extends/overrides broader. Agents write
    it, the user wins on conflict. Keep lean; detail in `.agents/{kb,flows,references}/`.
  (This global file is neither.) Shape/standards in `~/.agents/flows/REPO.md`.
- **Plans**: always `<project>/.agents/plans/<name>.md`, snake_case; sub-plans at
  `.../<name>/<sub>.md`; finished → `plans/completed/` (preserve sub-tree).
  **`~/.agents/plans/` is never a plan home** — re-home harness scratch into the
  project and delete the copy.
- **Global-config misses**: if these instructions caused a mistake or rework, or you
  have an improvement idea, drop a note in `~/.agents/dotagents/findings/` and move on
  — don't edit the config. Triage later folds them into `.../dotagents/DECISIONS.md`
  and moves each (never deletes) to `.../findings/processed/`.
- **Draft follow-ups**: adjacent work found mid-execution gets a `Status: draft` plan
  (idea + scope + why) in the project's `.agents/plans/` — never executed in the same pass.

## Load on demand
Read the matching file BEFORE such a task; skip it otherwise, never preemptively.
No flows ship yet — grow this list as you add `~/.agents/flows/`,
`~/.agents/kb/<language>.md`, or named-agent `~/.agents/<agent>.md` files.
<!-- dotagents:end -->
