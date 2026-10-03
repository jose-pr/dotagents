# Leak scan

What must never reach a public repository, and the tool that looks for it.

## What a leak is

Text in a tracked file, or in a commit message, that points a reader at
something they cannot open or that identifies what is private:

- a path into the private notes directory;
- a plan, by file name or by phase; a finding; a decision, by id;
- a user name, a home-directory path, a private repository's name;
- internal infrastructure: host names, private address ranges, test accounts,
  credentials, management endpoints;
- agent attribution in a commit message: a co-author trailer naming an
  assistant, a session trailer, a session link, a "generated with" footer.

The writing rule behind the first two is
`$ENGINEERING_OVERLAY_ROOT/kb/COMMENTS.md`. This file is the check.

## Running it

```bash
dotagents leak-check <repo>
dotagents leak-check <repo> --commits-only
```

Over MCP it is the tool `dotagents.leak-check`, with the same arguments.

It scans the working tree — tracked and untracked, ignore rules honoured,
since a file just written is where a new leak is likeliest — and every commit
message. `--commits-only` skips the tree, for a repository that tracks text
about the private notes on purpose.

**Judge it by its exit code**: 0 pass, 1 hits, 2 usage, 3 it could not check.
Read the header it prints: it names how files were enumerated and how many
patterns of each class were loaded. A class that did not run is reported as
`INCOMPLETE` in the verdict, never as a pass. Do not pipe it through `tail`;
the pipe returns tail's status and the verdict is lost.

A hit is judged, not obeyed: fix the file, or accept it knowingly. A
repository whose subject is the private notes themselves will always have
path hits.

## When

- before every handoff that commits or edits public-facing text;
- before every push to a public remote — CI is too late, the commit is
  already public;
- before a release tag, as the last gate, not the only one.

## The machine-local pattern file

The built-in patterns know the shapes: note paths, phase phrasing, decision
ids, plan file names, attribution trailers. They cannot know your names.
Those go in `audit_patterns.local.json` in the agent store
(`$DOTAGENTS_AUDIT_PATTERNS` or `--patterns` point elsewhere):

```json
{
  "personal": ["<user name>", "<private repository name>"],
  "public_allowlist": ["https://github.com/<published org>"],
  "infra": [
    {"id": "lab-host",
     "why": "internal host names",
     "regex": "(?<![0-9A-Za-z_])(?:buildhost|labsrv)(?![0-9A-Za-z_])",
     "ignorecase": true}
  ]
}
```

- `*.local.*` keeps the file out of every repository. It is never committed
  and never copied into shared configuration.
- An `infra` regex needs explicit boundaries and should match only what is
  yours: a rule that fires on ordinary words or on documentation address
  ranges gets ignored, and then it protects nothing.
- An allowlist entry is a rule that always says yes. When an allowlisted
  value turns out to identify something private, change the value; do not
  keep exempting it.
- Test each rule both ways: text it must match, and ordinary text it must
  not.

## A tree that ships nowhere

Workspace machinery with no consumer declares it with a
`.leak-check-ships-nowhere` file at its root:

```text
ships: nowhere
reason: <why nothing here reaches a consumer>
```

The tool refuses the marker when the tree has a git remote or a packaging
manifest, so it cannot silence a tree that ships. An honoured marker still
prints every hit.

## What the scan does not cover

- **History.** It reads the working tree and commit messages, not old blobs.
  A leak removed from the tree is still in every clone.
- **Identity.** A commit's author and committer fields are not checked.
  Verify `git config user.name` and `user.email` before the first commit in
  a repository.
- **What a pattern cannot express.** A hardware serial or a ticket number
  looks like any other token. Add such values as literals.

## Making it a test

A repository can enforce its own label shapes in its suite: a test that lists
the tracked files, scans them for the project's private label patterns, and
fails naming each `path:line`. Pair it with a test that plants one offender
per pattern, assembled from pieces at run time so the test file does not
contain them, so that loosening a pattern fails the suite.
