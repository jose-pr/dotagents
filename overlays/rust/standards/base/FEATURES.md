# Standard: Cargo features

Base standard: applies to every crate with a feature. Index:
[README.md](../README.md). How features are declared is in
`../../kb/RUST.md`; this file is what they mean and how they behave.

## A default build

- **A library's default build pulls in no optional dependency**:
  `default = []`. A consumer that wants one capability does not compile the
  others, and a crate meant to be linked into someone else's binary costs
  them nothing they did not ask for. (**house**)
- **A binary, a plugin or a crate whose only purpose needs a dependency** may
  turn it on by default, and says why in a comment beside `default`. A
  default feature that needs system libraries (pkg-config, a C toolchain, a
  framework) is named in the README's Development section, because it is
  what breaks a fresh build on another platform.
- `cargo build --no-default-features` and `cargo build --all-features` both
  succeed, and CI runs both.

## Naming and shape

- **One feature per capability**, named for what the user gets: `json`,
  `load`, `derive`, `regex`, `c-header`. kebab-case. Not the dependency's
  name unless the two are the same word, and never a bundle of unrelated
  things.
- **`dep:` for every optional dependency**, so no implicit feature named
  after a crate appears in the public surface.
- A feature that only enables another crate's feature is
  `"<crate>/<feature>"`; one that should not force the dependency on is
  `"<crate>?/<feature>"`.
- **No `full` or `all` feature** that is anything other than the union of
  the capability features, and no capability reachable only through it.

## Features are additive

Cargo unifies a dependency's features across everything in one build, so a
feature is a promise to every other crate in that build, not only to the one
that turned it on.

- **Turning a feature on never changes the behaviour of code that does not
  use it.** It adds items; it does not change what an existing function
  returns.
- **A dependency feature with global effect is its own opt-in feature,
  never part of a default.** `serde_json/arbitrary_precision` and
  `serde_json/preserve_order` change how *every* crate in the build that
  uses that dependency behaves.
  `arbitrary_precision` in particular breaks `#[serde(untagged)]` and
  `#[serde(flatten)]` numbers in unrelated crates. Name it for what it
  does (`exact-numbers`) and document what it costs the rest of the build.
- **A format dependency feeding an ordered model carries its
  order-preserving feature** (`toml/preserve_order`): otherwise the reader
  sorts what the model promised to keep.
- **Features do not gate symbols a linker resolves by name.** Unification
  turns a feature on for every crate in a workspace build, so "only one
  crate enables the exports" does not hold; put the symbol in a crate of its
  own instead ([PLUGINS.md](../specialized/PLUGINS.md)).

## When a feature is off

- **An item that needs a feature is absent without it**, at compile time,
  and its docs say which feature to enable.
- **A feature that enforces something says when it is not enforced.** A
  check that silently passes because its engine is compiled out (a `pattern`
  without a regex engine, a signature without a crypto backend) is exposed
  as a constant or a method the caller can test, and documented at the
  function that skips it.

## Documentation

- The README's Installation section and the shipped `AGENTS.md` have the
  same features table: flag, what it adds, what it is needed for, default.
- `[package.metadata.docs.rs] all-features = true`
  ([DOCS.md](DOCS.md), rustdoc).

## Tests

- Tests that need a feature are gated with `#[cfg(feature = "...")]` at the
  module or the file (`required-features` on a `[[test]]` target), and CI
  runs with `--all-features` so none is silently skipped.
- A test asserts the documented behaviour with the feature off where that
  behaviour is a promise (an item absent, a check reported as skipped).
