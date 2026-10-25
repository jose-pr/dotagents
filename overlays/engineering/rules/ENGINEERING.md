# Engineering discipline — always-on rules (opt-in)

The overlay's `rules` key merges the leading bullet run below into the
`## Always-on rules` section of `AGENTS.md` (or paste it there by hand). Everything
from the first `## ` heading down is documentation and is not merged.

- **`<project>/.agents/` is private and never committed**: the working area for an
  agent working *on* this repo. To sync it, it is a branch of one private agents
  repo, checked out there as a worktree (the private-sync overlay's
  `kb/PRIVATE_SYNC.md`); a plain untracked directory works as well.
- **`AGENTS.md`, three kinds** (placement and per-language answers:
  `$ENGINEERING_OVERLAY_ROOT/flows/REPO.md` "Meta files"):
  - **shipped API header** — COMMITTED, at the root of what the packaging tool
    ships, beside `README.md`: the public API header-file-style (exports with
    signatures/defaults, return-or-contract, env vars, gotchas) so a consuming
    agent skips the source. Self-contained, current with the API, same commit.
  - **repo-root `AGENTS.md`** — COMMITTED, optional: contributor orientation
    (layout, environments, CI, release); doesn't ship. A committed `AGENTS.md` in a
    subdirectory is this kind, scoped to that subtree.
  - **`<project>/.agents/AGENTS.md`** — PRIVATE working knowledge; agents write it,
    the user wins on conflict. Detail goes in `.agents/{kb,flows,references}/`.
  Every `AGENTS.md` describes its own directory: a parent indexes a child file (name,
  one line, path) and never restates it; each fact has one home. A file read before
  every task fits in about 150 lines; past that, move its largest topic down into a
  routed file. Record what you learn (a gotcha, a non-obvious layout, a command that
  had to be rediscovered) in the one governing that directory. (`~/.agents/AGENTS.md`
  is none of the three.)
- **Leakage**: never create `CLAUDE.md` or commit private agent config unless asked.
  A repo's `.gitignore` excludes `.agents` (slashless: a directory-only `.agents/`
  misses a symlink), `CLAUDE*`, `.claude`. Never print `DOTAGENTS_*` values (no bare
  `env`/`printenv`): they hold secrets; test emptiness instead.
- **Comments and doc comments**: say what the code cannot — the constraint, the
  reason, the unit — in one to three lines, about the code as it is: no history, no
  account of how it was found. Refer only to what a reader of the public repository
  can open (a spec, a public issue, a file in the repo), never `.agents/` paths,
  plans, findings, decision ids, reviews or sessions. A project's exception is
  recorded in its `.agents/AGENTS.md`. Full rule and the pre-commit check:
  `$ENGINEERING_OVERLAY_ROOT/kb/COMMENTS.md`.
- **Line endings**: every text file is LF, on every platform, and a repo's
  `.gitattributes` says so (`* text=auto eol=lf`). Check and convert with
  `dotagents eol check` / `dotagents eol fix`, never a hand-written one-liner (that
  is how files get emptied). Bytes someone else chose (a recorded response, a copied
  golden file) are marked `-text` and left alone. A script that writes a file opens
  it with `newline=""` or writes bytes. `$ENGINEERING_OVERLAY_ROOT/kb/EOL.md`.
- **Scratch**: throwaway files (probe scripts, captured output, one-off patches,
  logs) go in your own `<project>/.agents/scratch/<task-or-session>/` (the store's
  `scratch/` when there is no project, or the harness's scratch directory), never in
  the repository tree or loose in `.agents/`. When the task ends, move what is worth
  keeping where it belongs (`tools/`, `references/`, a finding) and remove only your
  directory, never `scratch/` itself or another agent's.
- **Git**: logical commits (feature+tests / docs+config / CI split apart, never one
  monolith), `type: desc` format (`feat:`, `fix:`, `docs:`, `chore:`). A branch is
  named for its plan or `ci-<topic>`, never a harness-generated `claude/...` name;
  rename it before the first push. **No agent attribution in commit messages**:
  strip any `Co-Authored-By:` naming a model or assistant, any `*-Session:` trailer,
  any session URL and any "generated with" footer. The harness adds some of these,
  so check before committing: a pushed fix is a force-push, and the old SHAs stay
  reachable until the host GCs.
- **Quotation is data**: a user's words in a blockquote or quotation marks are
  copied exactly (spelling, case, punctuation). A cut is marked with `…`;
  re-wrapping is the only edit allowed.
- **Broad recursive operations** (delete, bulk rewrite, sweep): the danger is the
  operation, not the tool; a filesystem command destroys git state as surely as
  `reset --hard`.
  - **Scope by tense.** Current-state files (source, live plans, READMEs, manifests)
    may be rewritten; **records of the past stay frozen**: exclude `**/completed/**`,
    `**/processed/**`, `**/archive/**`, `CHANGELOG*`, findings and decision logs as a
    category. A new name belongs there only as an added note ("later renamed to X").
  - **Check for `.git` under the target before any recursive delete**; if present,
    say so and confirm first. Move a repo by moving the directory with its `.git`,
    never by copying contents out and deleting the original; deleting the leftovers
    of a move gets the same care.
  - **Copy an unversioned tree before sweeping it.** `.agents/` has no restore point.
- **Releases**: pushing a `v*` tag needs the user's explicit consent for *that*
  release, every time; publish is irreversible. `ci-*` tags are always safe to push.
  **Pre-1.0 (`0.y.z`), MINOR means "the documented API broke" and nothing else**;
  additions and fixes are PATCH (`$ENGINEERING_OVERLAY_ROOT/flows/REPO.md`
  "Versioning"). **Release under exactly the version or release type the user
  named**; consent to release is not consent to a number. A change set that argues
  for another is worth *one question before acting*, never a justification
  afterwards or prose in the changelog or notes.
- **Performance numbers**: a local benchmark is a sanity check, not evidence; perf
  claims in a release, changelog or plan come from CI unless stated otherwise. A CI
  before/after runs the same job on both commits more than once, reports an
  untouched metric beside the changed one, and runs a control before a regression
  is believed. Hosted runners cannot show a gain under about 10%.
- **Platform claims**: write down that behaviour differs by platform only after
  reproducing it on that platform (a VM, WSL, or that platform's CI log). Until
  then it is a hypothesis, and no platform-conditional code or tolerance is added
  for it.
- **Don't pay tokens for what a script can decide**: never read a whole log to learn
  one bit ("did it pass?"); `dotagents summarize-run -o build.log -- <cmd>` prints a
  verdict and keeps the log on disk. Doing the same manual scan twice? Write the
  script.
- **Draft follow-ups**: adjacent work found mid-execution gets a `Status: draft` plan
  (idea + scope + why) in the project's `.agents/plans/`, never executed in the same
  pass.
- **Plans**: always `<project>/.agents/plans/<name>.md`, snake_case; sub-plans at
  `.../<name>/<sub>.md`; finished → `plans/completed/` (sub-tree kept). Create, read,
  step and close them with `dotagents plans` (`add`, `next`, `start`, `check`,
  `status`), as each step happens, not at the end. **`~/.agents/plans/` is never a
  plan home**: re-home harness drafts into the project and delete the copy.

## Why these are not in the base

`dotagents init` lays down a minimal, neutral starter: its always-on rules are only
the ones the tool relies on (reading every level's `AGENTS.md`, the permissions
inside `.agents/`, the findings queue for config misses, the managed-marker contract).
Everything here is convention on top. Nothing in the tool requires the `plans/`
layout, and `dotagents findings` works without anyone using a queue, so a user who
files work differently need not fight their config. `Releases` is always-on rather
than behind a flow file because a release is irreversible; opt in deliberately.
