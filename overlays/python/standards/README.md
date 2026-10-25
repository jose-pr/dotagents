# Python standards

How a Python library or tool is named, laid out, documented and tested.
Packaging, tooling, CI and release are in `../kb/PYTHON.md` and
`$ENGINEERING_OVERLAY_ROOT/flows/REPO.md`; nothing here restates them.

**Base** standards bind every project. A **specialized** standard adds to the
base for one kind of project or feature, and never overrides it.

## Base: every project

| File | Read before |
| --- | --- |
| [base/API.md](base/API.md) | naming or renaming anything public, adding a module, an exception or an async variant |
| [base/DOCS.md](base/DOCS.md) | writing or editing `README.md`, a shipped `AGENTS.md`, or the root `AGENTS.md` |
| [base/DEPENDENCIES.md](base/DEPENDENCIES.md) | writing network, path, copy or sync, configuration-file, TLS, proxy or HTTP code, or a command line; adding a dependency |
| [base/EXTRAS.md](base/EXTRAS.md) | adding an optional dependency or a feature that needs one |
| [base/CONVERSIONS.md](base/CONVERSIONS.md) | adding a `parse`, a `from_` or `to_` method, or any other way into or out of a type |
| [base/VALUES.md](base/VALUES.md) | adding a type that stands for a value: an address, a name, a version |
| [base/CONFIG.md](base/CONFIG.md) | reading an environment variable or a configuration file; handling a secret |
| [base/TESTING.md](base/TESTING.md) | writing or reviewing tests |
| [base/TYPING.md](base/TYPING.md) | annotating a public API; setting up a type checker |

Comments and docstrings follow `$ENGINEERING_OVERLAY_ROOT/kb/COMMENTS.md`.

## Specialized: when the project is of this kind

| File | Applies when |
| --- | --- |
| [specialized/PROTOCOL.md](specialized/PROTOCOL.md) | the library speaks a wire protocol: client, server, relay, codec |
| [specialized/LOADERS.md](specialized/LOADERS.md) | the library turns text or files into data: a configuration loader, a data-file backend, an expression parser |
| [specialized/EXTENSIONS.md](specialized/EXTENSIONS.md) | the library has interchangeable backends, formats, schemes or providers found by name |
| [specialized/CONFORMANCE.md](specialized/CONFORMANCE.md) | the library reimplements what another implementation defines |
| [specialized/REMOTE.md](specialized/REMOTE.md) | the library is a client of a service over a network |
| [specialized/SUBPROCESS.md](specialized/SUBPROCESS.md) | the library runs an external program |
| [specialized/CLI.md](specialized/CLI.md) | the library ships a command line as a feature |
| [specialized/APPLICATION.md](specialized/APPLICATION.md) | the package's reason to exist is its command |

A project follows the base and every specialized standard that applies.

## How to read a rule

A rule that Python itself prescribes cites PEP 8 or the typing specification.
A rule that follows established practice names the libraries that do it.
A rule marked **house** is a choice between conventions that are both in use;
it is binding here and is not a claim about Python.

A project that predates a standard is brought to it deliberately, as its own
change, never as a side effect of unrelated work. A break (a renamed public
name, a changed signature) is made outright and versioned as one; no
compatibility alias, shim or deprecated path is kept beside the new one.

## Exceptions

A project records its departures under one `## Exceptions to the standards`
heading in its `.agents/AGENTS.md`. The section opens with `Last brought to
the standards: <date>`, then one bullet per departure: the rule (file and
heading), what the project does instead, and why.

Read it before "fixing" a departure, and before relying on a rule the project
has opted out of. A departure with no recorded exception is a defect to raise,
not a precedent to copy.

## Before a project is called conforming

- Every CapWords name spells its acronyms in capitals.
- Every public module has `__all__`; every other module starts with `_`.
- One exception base, in one exceptions module; malformed input and a
  caller's bad argument are different types.
- `X` and `AsyncX` are siblings with the same method names.
- Constructors do no I/O; every resource holder is a context manager.
- The library leaves signals, logging configuration and stdlib modules alone,
  and neither prints nor exits.
- Network helpers come from `netimps` and remote-capable paths, copy and sync
  from `pathapi` (`pathapi-uri` for URIs); no local reimplementation of either.
- `import <pkg>` works with no extra installed; a missing extra fails where
  it is used, naming the extra.
- One name per conversion; value types are immutable, hashable and have a
  real `repr`.
- Environment variables are prefixed, read when needed, and documented; no
  secret reaches a log, a message or a `repr`.
- Warnings are errors in the test run; skips are visible; expected failures
  are strict.
- The command line, if any, is built with `duho`, is a `cli` package or
  `cli.py` behind a `cli` extra, and holds no logic the library lacks.
- `README.md` has the standard badge row and section order; the shipped
  `AGENTS.md` has the standard opening and tail sections.
- Comments, docstrings and the shipped `AGENTS.md` describe the code as it
  is: no history.
- No comment or docstring refers to anything outside the public repository.
- Every departure from the above is recorded under "Exceptions to the
  standards" in the project's `.agents/AGENTS.md`.
