# Comments and doc comments

Applies to every comment, docstring or doc comment, log message and error
message in a tracked file, in every language and every change.

## To the point

- **Say what the code cannot**: the constraint, the reason, the unit, the
  invariant, the consequence that is not visible from the line itself.
- **One to three lines.** An explanation that needs more is a doc comment, or
  a page in the docs that the comment names.
- **Describe the code as it is.** No "used to", "previously", "now", "no
  longer", "was changed to". What changed, and why, is the commit message;
  what a user must know about the change is the changelog.
- **No account of the work**: how a defect was found, what was tried first,
  who asked, what a review said, how long it took.
- **Do not restate the code.** A comment that repeats the line below it is
  deleted.

## Only what a reader of the repository can follow

A comment may refer to things a stranger with the public repository can open:

- a published specification (an RFC, a PEP, a standard), with its section;
- a public issue or pull request, by URL;
- a file, function or test in the repository, by name;
- upstream documentation or an upstream bug, by URL;
- the versions and platform that make a stated fact reproducible.

A comment never refers to anything that is not in the repository:

- private working notes, or any path under `.agents/`;
- a plan, by name, number or phase (`Phase N`, "the hardening plan");
- a finding, by name or id;
- a decision, by id or as "the decision to ...";
- a review, an audit, a session, an agent, a model or a conversation;
- private hosts, machines, people or dates of internal events.

The test: could someone holding only the public repository act on this
sentence? If it sends them to something they cannot open, rewrite it to state
the fact itself, or remove it.

## Measurements

State the value and the conditions that produced it, not the occasion.

```text
Bad:  tells a story, cites a private record, dates an internal event.
      Measured 2026-03-04 during the delivery review (finding relay-12): the
      old code enumerated every adapter per packet, which is why this was
      added.

Good: the fact and what it depends on.
      Enumerating adapters costs ~1 ms on Linux and up to 40 ms on Windows
      with many adapters, so the result is cached for INTERFACE_CACHE_TTL
      seconds.
```

A date belongs only where the fact is expected to go stale, and then beside
the version it was true for.

## Doc comments

- First line: what the thing does or is, one sentence.
- A public function documents its arguments, its return value and what it
  raises or returns on failure. A private helper gets a line, or nothing.
- The doc comment is the contract; the reasoning behind an implementation
  choice is a comment at the line that makes it.

## `TODO`

A `TODO` names a public issue, or it is not written. Deferred work with no
public issue is tracked outside the source.

## Check

Before committing, search the staged diff for what this file forbids:

```bash
git diff --cached -U0 | grep -nE '\.agents|[Ff]indings?[ /:]|[Pp]lans?/|\bD[0-9]{2}\b|Phase [0-9]|\b(review|audit|session) (found|flagged|said)'
```

A hit is read and judged, not waved through: `plans/` in a path that exists in
the repository is fine. The leak scan
(`$ENGINEERING_OVERLAY_ROOT/kb/LEAKS.md`), run at every publishing handoff,
covers the same ground over the whole tree and the commit messages.

## Exceptions

A project that keeps a different convention records it in its
`.agents/AGENTS.md`. Without that record, this file applies.
