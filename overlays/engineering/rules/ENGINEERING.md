# Engineering discipline — always-on rules (opt-in)

Paste these into your `~/.agents/AGENTS.md` under `## Always-on rules` — or let the
overlay do it: this file is declared by the overlay's `rules` key, and applying the
overlay merges the leading bullet run below into that section. They are
**opinions**, not mechanisms: nothing in the `dotagents` CLI depends on them, which
is why they live here rather than in the neutral base overlay. Each one exists
because its absence cost something real (see the D-numbers in `design/`).

Everything from the first `## ` heading down is documentation, not merged.

- **`<project>/.agents/` is private and never committed**: it is the working area for
  an agent working *on* this repo. Keep it out of the project's history — a slashless
  `.agents` line in `.gitignore` (a directory-only `.agents/` won't match the symlink
  `dotagents link` creates). Linking it to a per-project store in one private repo is
  the sync workflow; a plain untracked directory works just as well.
- **`AGENTS.md`, three kinds** (placement rule and per-language answers:
  `$ENGINEERING_OVERLAY_ROOT/flows/REPO.md`):
  - **shipped API header** — COMMITTED, at the root of whatever the packaging tool
    ships, beside `README.md` (Python `src/<pkg>/`, Rust the crate root; per-module
    below that for large surfaces): public API header-file-style (exports with
    signatures/args/defaults, return-or-contract, env vars, gotchas) so a consuming
    agent skips the source. Self-contained, current with the API, same commit.
  - **repo-root `AGENTS.md`** — COMMITTED, optional: contributor orientation for a
    checkout (layout, environments, CI, release). Not the API header; doesn't ship.
  - **`<project>/.agents/AGENTS.md`** — PRIVATE working knowledge (architecture,
    gotchas, per-dir guidance); deeper subtree extends/overrides broader. Agents write
    it, the user wins on conflict. Keep lean; detail in `.agents/{kb,flows,references}/`.
  (The global `~/.agents/AGENTS.md` is none of them.) Record what you learn while working —
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
- **Broad recursive operations** (delete, bulk rewrite, sweep) — the dangerous class is
  the operation, not the tool; a filesystem command destroys git state just as well as
  `reset --hard` does (D11):
  - **Scope by tense, not by noise.** Current-state files (source, live plans, READMEs,
    manifests) may be rewritten; **records of the past must stay frozen** — exclude
    `**/completed/**`, `**/processed/**`, `**/archive/**`, `CHANGELOG*`, findings and
    decision logs as a category. Rewriting a name inside a finished record does not make
    it accurate, it makes it lie about history. Need the new name there? Add a note
    ("later renamed to X") — additive, never in place.
  - **Check for `.git` under the target before any recursive delete**; if present the
    operation destroys history — say so and confirm first. Move a repo by moving the
    directory with its `.git`, never by copying contents out and deleting the original.
    "Delete the leftovers after a move" gets the same care as the move — the leftovers
    are where the metadata lives.
  - **Copy an unversioned tree before sweeping it.** `.agents/` has no restore point,
    which earns it more care than tracked source, not less.
- **Releases**: pushing a `v*` tag requires the user's explicit consent for *that*
  release, every time — publish is irreversible. `ci-*` tags are always safe to push.
  **Pre-1.0 (`0.y.z`), MINOR means "the documented API broke" and nothing else** —
  new methods, new optional kwargs and fixes are all PATCH, so a `~=0.9.0`
  subscriber gets additions without a re-read and a minor bump stays a real
  signal. Additive API is *not* a minor before 1.0 (it is after). Full rule and
  rationale: `$ENGINEERING_OVERLAY_ROOT/flows/REPO.md` "Versioning".
  **Release under exactly the version or release type the user named** — consent to
  release is not consent to a number. A change set that argues for a different one is
  worth *one question before acting* ("this renames a public subpackage — still
  0.2.3?"), never a justification afterwards, and never prose in the changelog or
  release notes arguing for a choice the user did not make
  (D13).
- **Performance numbers**: a local benchmark is a sanity check, not evidence — perf
  claims in a release, changelog, or plan come from CI unless stated otherwise.
- **Don't pay tokens for what a script can decide**: never dump a whole log into
  context to learn one bit ("did it pass?") — pipe it through something that prints a
  verdict, keeping the log on disk.
  `$ENGINEERING_OVERLAY_ROOT/tools/summarize_run.py --log build.log -- <cmd>` does exactly
  this (the flag is `--log`, not `--log-file`). Doing the same manual scan twice? Write
  the script.
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
tool reads or requires that layout. `findings/` has a command (`dotagents findings`
manages `<scope>/findings/`), but using it is likewise opt-in: nothing else in the
tool depends on a findings queue existing. A user who files work differently
should not have to fight their own config.

`Releases` in particular is opinionated *and* important (D02
argues it must be always-on rather than gated behind loading a flow file). Opt in
deliberately rather than inheriting it silently.
