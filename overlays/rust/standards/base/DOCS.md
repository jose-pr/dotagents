# Standard: README, AGENTS.md and rustdoc

Base standard. Every crate's `README.md` and `AGENTS.md` have the same shape,
so a reader who knows one knows where to look in the next. Index:
[README.md](../README.md). Which `AGENTS.md` is which, and what ships, is in
`$ENGINEERING_OVERLAY_ROOT/flows/REPO.md`; this file fixes their format.

## `README.md`

Every crate that is published has one beside its `Cargo.toml`
(`readme = "README.md"`); it is what crates.io renders. A workspace has one
at the root as well.

### Title and badges

The title is the package name. The badge row follows it directly: these
five badges, in this order, with this alt text, one per line.

```markdown
# <crate>

[![Version](https://img.shields.io/crates/v/<crate>.svg)](https://crates.io/crates/<crate>)
[![Rust version](https://img.shields.io/crates/msrv/<crate>.svg)](https://crates.io/crates/<crate>)
[![License: <SPDX>](https://img.shields.io/badge/license-<SPDX>-blue.svg)](https://github.com/<gh_org>/<repo>/blob/main/LICENSE)
[![Docs](https://img.shields.io/docsrs/<crate>)](https://docs.rs/<crate>)
[![CI](https://img.shields.io/github/actions/workflow/status/<gh_org>/<repo>/test.yaml)](https://github.com/<gh_org>/<repo>/actions/workflows/test.yaml)
```

1. Version
2. Rust version (the MSRV)
3. License
4. Docs
5. CI

- No badge is added, dropped or reordered. An unlicensed project omits the
  License badge and section, and is `publish = false` until one is chosen.
- **Docs is docs.rs on a crate's README.** On a workspace root whose project
  has a narrative site, Docs points at the site instead. (**house**)
- A workspace root whose project also ships packages in other languages adds
  their registry badges after Rust version, in the order the README lists
  the languages.
- The CI badge points at the test workflow, never the release workflow.
- Every link is absolute; crates.io breaks a relative one.

### Opening

Two to four sentences after the badges: what the crate is, with the core
promise in bold, and a link to the docs.

### Sections

These headings, with this spelling, in this order:

| Heading | Holds |
| --- | --- |
| `## Features` | what a user gets, as bullets or a capability table |
| `## Installation` | `cargo add <crate>`, then a table of Cargo features |
| `## Quick start` | one runnable snippet per headline capability, smallest first |
| `## Command line` | only when the package builds a binary; each command runs as written |
| `## API overview` | a table of public modules and their purpose |
| `## Development` | the check commands, then `### Releasing` |
| `## License` | one line and a link |

A section specific to the project goes between `API overview` and
`Development`. Not `Install`, `Usage`, `Quickstart`, `CLI` or `Licence`.

**The README is the crate's root documentation**:
`#![doc = include_str!("../README.md")]` in `lib.rs`, so every snippet in it
is a doctest and cannot rot. (**house**) The workspace root's README, which
no crate includes, is kept to snippets a test compiles, or to none.

## Shipped `AGENTS.md` (`<crate>/AGENTS.md`)

The API header a consumer reads instead of the source. `cargo package
--list -p <crate>` must show it.

### Opening

```markdown
# `<crate>` — API header

Header-file-style reference for the `<crate>` crate: every public item with
its signature, contract and gotchas, so the crate can be used without
reading its source. It ships inside the package and is self-contained.
Development documentation lives with the source at
<https://github.com/<gh_org>/<repo>>.
```

Then, in this order:

1. a features table (flag, what it adds, default), the MSRV, and what a
   default build depends on;
2. where items are imported from: what the root re-exports, which modules
   are public;
3. whether the crate holds `unsafe`, and where.

### Body

- One `##` section per public module or topic, in the order a user meets
  them. The heading names the path: `## Registry (`<crate>::library`)`.
- Each entry gives the signature in a code block, then what it returns or
  guarantees, then its arguments, then its errors and panics.
- A trait a caller implements lists its required and provided methods, and
  what a default body does.

### Tail

These sections close the file, in this order, each present only when it has
content:

1. `## Errors`
2. `## The C surface` (an FFI crate: the header, the symbol prefix, ownership)
3. `## Command line`
4. `## Environment variables`
5. `## Gotchas`

### Rules

- **It describes the API as it is.** No history, no "superseded", no "it was
  UB before". A changed behaviour is a changelog entry.
- **Every name it gives exists.** A function, feature, version or file the
  header names is checked against the crate when the header changes; a
  header naming a function that moved to another crate is a defect.
- **Self-contained**: no repo-relative links. An installed consumer has no
  repository.
- **Current in the same commit** as the API change it describes.
- A large surface adds per-module headers, `src/<module>/AGENTS.md`, in the
  same shape; the crate header lists them.

## Repo-root `AGENTS.md`

Contributor orientation for a checkout. It does not ship. These sections,
in this order:

1. a table of the shipped headers and what each covers
2. `## Layout`
3. `## Commands`
4. `## Rules`
5. `## CI and release`

It links freely within the repository and never into private working notes.

## rustdoc

- `#![warn(missing_docs)]` (and `missing_debug_implementations` in a library)
  at every published crate's root; CI builds docs with
  `RUSTDOCFLAGS="-D warnings"`.
- First line: what the item does or is, one sentence. Then `# Errors`,
  `# Panics` and `# Safety` sections wherever they apply (`C-FAILURE`).
- **Examples use `?`, not `unwrap`** (`C-QUESTION-MARK`), and run. An example
  that cannot run in a test is `no_run`; one that cannot compile there
  (it needs a `cdylib` or a symbol another crate defines) is `ignore` with
  the reason on the fence line. A refusal the API promises is pinned by a
  `compile_fail` example.
- `[package.metadata.docs.rs] all-features = true` and
  `rustdoc-args = ["--cfg", "docsrs"]`, with
  `#![cfg_attr(docsrs, feature(doc_cfg))]` at the root, so docs.rs marks
  each feature-gated item with the feature it needs. (tokio, serde_with.)
- A `///` on a `mod` declaration merges with that module's `//!` header;
  write module documentation in one of the two places, not both.
