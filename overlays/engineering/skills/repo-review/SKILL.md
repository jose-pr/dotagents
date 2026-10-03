---
name: repo-review
description: Deep, whole-repository review, run rarely (after a few release cycles) — does the project meet its goals, follow its standards, hold up for correctness and soundness, and stay lean; what is missing or could be better. Records verified findings where the project keeps them and, when asked, writes plans to address them. Use when asked for a deep, thorough or full review or audit of a repo. Not for reviewing a diff or a pull request.
---

# Repository review

A periodic, whole-repository review. It answers four questions with evidence:

1. Does the project do what it says it is for?
2. Does it follow the standards that apply to it?
3. Is it correct and sound?
4. Is it lean — is each thing done the simplest way that performs as well?

and one forward-looking one: what is missing, and what would make it better?

It produces **findings**, recorded where the project keeps them, and a
**review record**. It changes no code. Plans come afterwards, only when asked.

## Tools

This review reads and writes through `dotagents` commands. Where the harness has
`dotagents` registered as an MCP server (`DOTAGENTS_MCP=stdio`), use the tools; a
review records many findings, and a tool call costs far less than a process each.

| Tool | Command line | For |
| --- | --- | --- |
| `dotagents.findings.list`, `.show` | `dotagents findings list` | what is already known |
| `dotagents.findings.add` | `dotagents findings add "<line>" --body-file <file>` | recording a finding |
| `dotagents.plans.list`, `.next` | `dotagents plans list` | live plans, so nothing planned is re-raised |
| `dotagents.plans.add`, `.validate` | `dotagents plans add <name> -t ... -p ...` | writing plans, when asked |
| `dotagents.leak-check` | `dotagents leak-check <repo>` | the leak scan in the baseline |
| `dotagents.summarize-run` | `dotagents summarize-run -- <command>` | each baseline check: a verdict, not a log |
| `dotagents.eol.check` | `dotagents eol check` | line endings |
| `dotagents.compare-bench` | `dotagents compare-bench <old> <new>` | benchmark results across releases |

## Arguments

- A path or area narrows the scope; default is the whole repository.
- `plans` — after recording, also write plans (see "Plans").
- `issues` — record public-safe findings in the host's issue tracker instead
  of the private queue (see "Where findings go").

## Ground rules

- **Evidence, not opinion.** A finding states what was observed, what was
  expected and by what authority (a spec, a standard's rule, the project's
  own documented claim), and the command that shows it. What can be run is
  run, not inferred from reading.
- **Review, do not fix.** No code changes, no drive-by cleanups, no commits.
- **Known is not new.** An open finding, an existing plan, a recorded
  decision or a recorded exception is not re-raised. Raise a decision again
  only with evidence it did not have.
- **Name things, do not number lines.** Cite a module, class or function by
  name; line numbers are stale by the time anyone acts.
- **Say what was not checked.** Silence about an area must mean it was
  reviewed and found clean, so anything skipped is listed.
- **Nothing private goes anywhere public**, and nothing about a security
  weakness goes anywhere public at all.

## 1. Orient

Before judging anything:

- Read every `AGENTS.md` governing the repository, the README, the shipped
  API header, and the changelog since the last review.
- Read the open findings, live plans, decisions and recorded exceptions.
- **Write down the project's goals** as the project states them: the README's
  promise, its feature and coverage tables, its roadmap. If the goals are not
  stated anywhere, say so — that is the first finding — and record the goals
  you assumed.
- Pin what is being reviewed: the commit, the version, a clean tree.
- Find the previous review record. Note what changed since, and list the
  findings it closed, to check they stayed closed.
- List the standards that apply: the repository standard
  (`$ENGINEERING_OVERLAY_ROOT/flows/REPO.md`), the comments rule
  (`$ENGINEERING_OVERLAY_ROOT/kb/COMMENTS.md`), the language's own documents
  and library standards (`$<LANG>_OVERLAY_ROOT/`), base and each specialized
  one that fits.

## 2. Baseline

Run the project's own checks exactly as it documents them, on the oldest and
newest supported runtime: tests, type check, formatter, docs build, package
build, and the benchmarks if it has any. Run each through
`dotagents summarize-run -- <command>`. Run the leak scan
(`$ENGINEERING_OVERLAY_ROOT/kb/LEAKS.md`) too. A check that fails, is
skipped without saying so, or cannot be run as documented is a finding before
the review proper begins. Build the package and install it into a clean
environment; the rest of the review uses what a user would get.

## 3. Review

One pass per lens. Where the harness runs subagents, split the code into
areas and give each area's reviewer the goals, the pinned commit and the
lenses; use the `reviewer/code` and `reviewer/security` roles from
`$ENGINEERING_OVERLAY_ROOT/kb/MODELS.md`. State the planned scale before
starting.

| Lens | Asks |
| --- | --- |
| Goals | For each stated goal and feature: is it met, and what shows it? Follow the README from a clean install; run every example. What does the project claim that it does not do? |
| Standards | Walk each applicable standard's checklist. Every departure is either a recorded exception or a finding. |
| Correctness | Bugs, unhandled edges, error paths, resource leaks, concurrency, behaviour that differs by platform or runtime version. Tests that assert the bug instead of the contract. |
| Soundness | Input from outside: bounds, validation, what a hostile or malformed input can cost. Secrets in logs, messages and reprs. Subprocesses, paths, temporary files, defaults that are unsafe. |
| API | Is the public surface coherent, minimal and hard to misuse? What would a user of this kind of library expect that is absent? What is public that should not be? Compare with two or three comparable libraries and with the standard library's nearest analogue. |
| Lean | Duplication, dead code, indirection that buys nothing, options nobody needs, modules too large to review, a hand-written version of something a preferred or standard library already does. |
| Performance | Hot paths, complexity, the trend in the tracked benchmark results across releases. |
| Tests | Do they test behaviour or implementation? What is mocked that should be real? Silent skips. Risky paths with no test. Checks against a real peer or reference implementation. |
| Docs | README, shipped header, docs site and changelog against the code as it is. |
| Packaging | What actually ships; metadata; extras; dependency ranges and their floors; unused, abandoned or wrongly licensed dependencies; the three workflows. |
| Consumers | Who uses this project, here or publicly: what do they work around, reimplement or wrap? |

**The Lean lens has a higher bar.** "This could be cleaner" is a finding only
with the cleaner form sketched, the reason behaviour is unchanged, and — on a
measured path — the reason performance is unchanged. When the current way
turns out to be the best one, record that too, under "Considered and kept",
with the reason, so the next review does not ask again.

**Missing features and better approaches are proposals, not defects.** Record
them separately (below), each with who needs it and what it would cost.

## 4. Verify

Nothing is recorded on one reading.

- Each candidate is reproduced or refuted by a pass other than the one that
  raised it: run the repro, read the code path, check the claim against the
  spec. A finding that cannot be run is marked `read-only`.
- A refuted candidate is kept in the review record with the reason, so it is
  not raised again.
- A completeness pass asks which areas and lenses produced nothing, and why,
  and sends reviewers back where the answer is "nobody looked".

## 5. Record

### Where findings go

Use the project's own way, found in this order:

1. what its `AGENTS.md` says;
2. its findings queue — `dotagents findings add "<line>" --body-file <file>`
   in the project scope, then `dotagents findings index`;
3. the host's issue tracker, only when the `issues` argument was given or the
   project's notes say so: show the list first, get a yes, then create them.
   A security finding, and anything that names private material, stays in
   the private queue whatever was asked.

### One finding, one record

Each finding can be closed on its own. Themes are labels, not containers.

```text
[severity][lens] <the defect, as a statement>

Where:      <module>.<symbol>
Reviewed:   <short sha>
Observed:   <what happens>
Expected:   <what should, and the authority for it>
Repro:      <command, and its output>
Confidence: verified | read-only
Direction:  <one or two sentences; not a patch>
Effort:     S | M | L          Breaks API: yes | no
Related:    <other findings>
```

Severity: **critical** — data loss, a security hole, or the main purpose does
not work. **high** — a wrong result or crash in a supported, likely case; a
documented API that does not do what it says. **medium** — wrong in an
unlikely case; a standard broken in the public surface; a risky path with no
test. **low** — internal quality, minor drift. **nit** — style.

A proposal uses `[proposal][lens]` and replaces Observed/Expected/Repro with
Need, Sketch and Cost. A choice only the owner can make uses `[decision]` and
states the options and what each costs.

### The review record

One file, `<project>/.agents/reviews/<date>_<short sha>.md`:

- the goals as understood, and the commit, version and runtimes reviewed;
- baseline results;
- a coverage table: area by lens, reviewed or not, findings raised;
- counts by severity and lens, and the ten that matter most;
- regressions: findings a previous review closed that are open again;
- **Considered and kept**: what was examined and found right, with reasons;
- refuted candidates, with reasons;
- owner decisions needed;
- what was not reviewed, and why.

A pattern that no standard covers, or a standard that turned out wrong, is
recorded against the configuration (`dotagents findings add -g`), not the
project.

## 6. Report

Tell the owner, briefly: the verdict on each of the four questions, the
counts, the items that matter most, the decisions that are theirs, and where
the records are. Offer plans; do not write them unasked.

## Plans

Only when asked. Follow `$ENGINEERING_OVERLAY_ROOT/flows/PLAN.md`, starting
from its template.

- **Every finding lands somewhere**: in exactly one plan, or in a list of
  findings deliberately declined or deferred, each with its reason. The
  mapping is a table in the review record.
- Group by area and dependency, not by severity; order the plans so critical
  and high findings close first.
- **A plan's first step re-runs each finding's repro on the current tree.** A
  finding describes the commit it was reviewed at; some will already be fixed
  and some will have been wrong.
- A fix that breaks the documented API says so, with the version it implies.
- `[decision]` items are not planned around. They are put to the owner, and
  the plans that depend on them say which answer they assume.
- Plans are `Status: draft` until the owner approves them.
- A finding is closed with what was done and the evidence, by the plan that
  fixes it — never by the review.
