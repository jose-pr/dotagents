# Rust standards

How a Rust crate or workspace is named, laid out, documented and tested.
Packaging, tooling, CI, release and known traps are in `../kb/RUST.md` and
`$ENGINEERING_OVERLAY_ROOT/flows/REPO.md`; nothing here restates them.

**Base** standards bind every crate. A **specialized** standard adds to the
base for one kind of crate or feature, and never overrides it.

## Base: every crate

| File | Read before |
| --- | --- |
| [base/API.md](base/API.md) | naming or renaming anything public, adding a module, an error type, a constructor or an async API |
| [base/UNSAFE.md](base/UNSAFE.md) | writing `unsafe`, an `unsafe fn`, a type whose fields carry a safety invariant, or a macro that expands to `unsafe` |
| [base/DOCS.md](base/DOCS.md) | writing or editing `README.md`, a shipped `AGENTS.md`, the root `AGENTS.md`, or rustdoc |
| [base/DEPENDENCIES.md](base/DEPENDENCIES.md) | adding a dependency, or writing what a preferred crate already does |
| [base/FEATURES.md](base/FEATURES.md) | adding a Cargo feature, an optional dependency, or a feature of a dependency |
| [base/CONVERSIONS.md](base/CONVERSIONS.md) | adding a `From`, `TryFrom`, `FromStr`, `as_`/`to_`/`into_` method, or any other way into or out of a type |
| [base/VALUES.md](base/VALUES.md) | adding a type that stands for a value: an address, a name, a version, an identifier |
| [base/CONFIG.md](base/CONFIG.md) | reading an environment variable or a configuration file; handling a secret |
| [base/TESTING.md](base/TESTING.md) | writing or reviewing tests, lints or CI gates |

Comments and doc comments follow `$ENGINEERING_OVERLAY_ROOT/kb/COMMENTS.md`.

## Specialized: when the crate is of this kind

| File | Applies when |
| --- | --- |
| [specialized/FFI.md](specialized/FFI.md) | the crate exports or imports a C ABI, ships a C header, or is driven by bindings in another language |
| [specialized/PLUGINS.md](specialized/PLUGINS.md) | the project loads providers from shared libraries, or ships one |
| [specialized/PROTOCOL.md](specialized/PROTOCOL.md) | the crate speaks a wire protocol: client, server, codec |
| [specialized/LOADERS.md](specialized/LOADERS.md) | the crate turns text or files into data: a format, a configuration loader, a path or expression grammar |
| [specialized/APPLICATION.md](specialized/APPLICATION.md) | the package builds a binary (a daemon, a command, a worker), or a library ships a command line |

A crate follows the base and every specialized standard that applies.

## How to read a rule

A rule that Rust itself prescribes cites the
[Rust API Guidelines](https://rust-lang.github.io/api-guidelines/) by
identifier (`C-CASE`, `C-CONV`) or the standard library. A rule that follows
established practice names the crates that do it. A rule marked **house** is
a choice between conventions that are both in use; it is binding here and is
not a claim about Rust.

A crate that predates a standard is brought to it deliberately, as its own
change, never as a side effect of unrelated work. Changing a public name or
signature is an API break and is versioned as one (`REPO.md`, "Versioning").

## Exceptions

A project records its departures under one `## Exceptions to the standards`
heading in its `.agents/AGENTS.md`. The section opens with `Last brought to
the standards: <date>`, then one bullet per departure: the rule (file and
heading), what the project does instead, and why.

Read it before "fixing" a departure, and before relying on a rule the project
has opted out of. A departure with no recorded exception is a defect to raise,
not a precedent to copy.

## Before a crate is called conforming

- Names follow `C-CASE`: an acronym is one word (`HttpClient`, `Uuid`).
- The root re-exports what the common task needs; every other item has one
  public path, and nothing public is reachable only through a `#[doc(hidden)]`
  item a human is meant to call.
- No safe function can reach undefined behaviour for any input; a type whose
  fields carry a safety invariant keeps them private.
- `#![forbid(unsafe_code)]` everywhere `unsafe` is not needed, and a test
  names every file allowed to hold it.
- Public error and growing enums are `#[non_exhaustive]`; errors implement
  `std::error::Error` and never quote input.
- A library keeps no process-global state, installs no logger, and neither
  prints nor exits.
- Items are named from `core::` or `alloc::` wherever they live there, held by
  the `std_instead_of_core`/`std_instead_of_alloc` lints, so every `std::`
  path marks a real need for the operating system.
- A library's default build pulls in no optional dependency; each feature is
  one capability, additive, and listed in the README and the shipped header.
- Conversions use the standard traits; value types implement the common
  traits and a `Debug` that shows the value, never a pointer or a secret.
- Environment variables are prefixed, read when needed, and documented.
- A command line, if any, is built with `clap`'s derive API and holds no
  logic the library lacks.
- `cargo fmt --check`, clippy with `-D warnings` over all targets and
  features, and a strict `cargo doc` pass on stable and the MSRV.
- `README.md` has the standard badge row and section order; the shipped
  `AGENTS.md` has the standard opening and tail sections.
- Comments, doc comments and the shipped `AGENTS.md` describe the code as it
  is: no history.
- No comment, doc comment or error message refers to anything outside the
  public repository.
- Every departure from the above is recorded under "Exceptions to the
  standards" in the project's `.agents/AGENTS.md`.
