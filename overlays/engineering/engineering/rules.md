# Engineering discipline — always-on rules (opt-in)

`install --overlays <this>` merges the bullets below into your
`~/.agents/AGENTS.md` under `## Always-on rules` (declared by this overlay's
`rules` key). They are **opinions**, not mechanisms: nothing in the `dotagents`
CLI depends on them, which is why they live here rather than in the neutral base
overlay. Each one exists because its absence cost something real (see the
D-numbers in `design/`).

Everything from the first `## ` heading down is documentation, not merged.

- **`<project>/.agents/` is private and never committed**: it is the working area for
  an agent working *on* this repo. Keep it out of the project's history — a slashless
  `.agents` line in `.gitignore` (a directory-only `.agents/` won't match the symlink
  `dotagents link` creates). Linking it to a per-project store in one private repo is
  the sync workflow; a plain untracked directory works just as well.
- **`AGENTS.md`, two kinds** — no repo-root one:
  - **`<project>/src/**/AGENTS.md`** — COMMITTED, package-shipped "header" per module
    dir: that module's public API header-file-style (exports with signatures/args/
    defaults, return-or-contract, env vars, gotchas) so a consuming agent skips the
    source. Current with the API, same commit.
  - **`<project>/.agents/AGENTS.md`** — PRIVATE working knowledge (architecture,
    gotchas, per-dir guidance); deeper subtree extends/overrides broader. Agents write
    it, the user wins on conflict. Keep lean; detail in `.agents/{kb,flows,references}/`.
  (The global `~/.agents/AGENTS.md` is neither.) Record what you learn while working —
  a gotcha, a non-obvious layout, a command that had to be rediscovered — back into
  the one governing that directory, so the next session starts where this one ended.
- **Leakage**: never create `CLAUDE.md` or commit private agent config unless asked.
  Repo `.gitignore` excludes `.agents` (slashless — the link is a symlink, which a
  directory-only `.agents/` won't match), `CLAUDE*`, `.claude`. Never print `DOTAGENTS_*`
  values (no bare `env`/`printenv`) — they hold secrets; test emptiness instead.
- **Git**: logical commits (feature+tests / docs+config / CI split apart, never one
  monolith), `type: desc` format (`feat:`, `fix:`, `docs:`, `chore:`).
  **No agent attribution in commit messages** — strip any `Co-Authored-By:` naming a
  model/assistant, any `*-Session:` trailer, any session URL, and any "generated with"
  footer. The harness adds some of these automatically, so check before committing,
  not after: a session URL exposes an id, and the rest is noise in a human history.
  Already pushed? Rewriting is a force-push and the old SHAs stay reachable until the
  host GCs — so catch it while the commits are still local.
- **Releases**: pushing a `v*` tag requires the user's explicit consent for *that*
  release, every time — publish is irreversible. `ci-*` tags are always safe to push.
- **Performance numbers**: a local benchmark is a sanity check, not evidence — perf
  claims in a release, changelog, or plan come from CI unless stated otherwise.
- **Don't pay tokens for what a script can decide**: never dump a whole log into
  context to learn one bit ("did it pass?") — pipe it through something that prints a
  verdict, keeping the log on disk. Doing the same manual scan twice? Write the script.
- **Draft follow-ups**: adjacent work found mid-execution gets a `Status: draft` plan
  (idea + scope + why) in the project's `.agents/plans/` — never executed in the same
  pass.
- **Plans**: always `<project>/.agents/plans/<name>.md`, snake_case; sub-plans at
  `.../<name>/<sub>.md`; finished → `plans/completed/` (preserve sub-tree).
  **`~/.agents/plans/` is never a plan home** — re-home harness scratch into the
  project and delete the copy.

## Why these are not in the base

`dotagents init` promises "a minimal, neutral starter … imposes none of this repo's
own opinions". The base overlay therefore carries only what the tool's own code
depends on: `.agents` as the link target (`_link.py`), the two kinds of `AGENTS.md`,
the secrets/leakage guard, and the managed-marker contract (`_merge.py`).

Everything here is convention on top of that. `plans/` is a case in point — the CLI
seeds an empty `plans/` dir into a new store as a convenience, but nothing in the
tool reads or requires that layout, and `findings/` is not referenced by code at all.
A user who files work differently should not have to fight their own config.

`Releases` in particular is opinionated *and* important ([D02](../../design/decisions/D02.md)
argues it must be always-on rather than gated behind loading a flow file). Opt in
deliberately rather than inheriting it silently.
