# Standard: tests, lints and gates

Base standard: how tests are designed and what CI enforces. Index:
[README.md](../README.md). Running them — toolchains, the matrix, the
workflow templates — is in `../../kb/RUST.md`.

## Layout

```text
src/<module>.rs        #[cfg(test)] mod tests { .. } for private behaviour,
src/<module>/tests.rs  or beside it in its own file once it is long
tests/<behaviour>.rs   one integration binary per area of the public API
tests/support/mod.rs   helpers two or more test binaries share
examples/              runnable programs, built by `cargo test`
benches/               on demand, never per push (REPO.md "benchmarks")
```

- **An integration test uses the public API only**; what needs a private
  item is a unit test.
- **Shared helpers live in `tests/support/mod.rs`**, never `tests/common.rs`
  (Cargo would build that as a test binary of its own).
- **A test that measures process-wide state gets a binary of its own**: the
  first mapping of a library, a global allocator count, an environment
  variable. Two such tests in one binary measure each other.

## What a test proves

- **Names are sentences** that state the behaviour:
  `a_value_the_schema_marks_write_only_is_withheld`, not `test_redact_2`.
- **A test is seen to fail before it is trusted.** Break the code it covers
  and watch it go red; a test that stays green with the feature deleted
  proves nothing.
- **A bug fix arrives with the test that would have caught it**, named for
  the behaviour.
- **A test does not encode a bug.** When a test and a specification disagree,
  rewrite the test to the specification.
- **A refusal the API promises at compile time is a `compile_fail` doctest**
  (or a `trybuild` case), so the refusal cannot quietly become accepted.
- **A counting test counts something real**: a test of allocation through a
  custom allocator uses the constructors that take it, or it passes while
  measuring the global heap.

## Real over mocked

- **Mock what is not yours and cannot be run**: a remote peer, the clock,
  hardware. Not your own crates.
- The filesystem is real, under a unique temporary directory the test
  removes.
- A network test binds port 0 and reads the port back.
- **A library loaded at run time is a real library**, built by the test
  suite (an `examples/` or workspace `cdylib` taken as a dev-dependency), not
  a stub.
- Where the crate talks to something that exists — a protocol peer, a
  reference tool, a C compiler for a header — at least one test talks to the
  real one, and skips visibly where it is absent.

## Kinds worth having

- **Property or randomised tests** for anything that parses, encodes or
  round-trips: arbitrary input returns a value or an error, never a panic.
- **Contract pins**: a committed public-API snapshot, a committed C header
  with a drift test, the hashes or layouts other binaries depend on. Each
  fails on any difference and is regenerated in the commit that means it.
- **Guard tests for house rules**: the files allowed to hold `unsafe`
  ([UNSAFE.md](UNSAFE.md)), the statics allowed in a library
  ([API.md](API.md)). A rule a test checks stays true.
- **Old binaries against the new code**, where compatibility with built
  artefacts is promised ([FFI.md](../specialized/FFI.md)).
- The README's and the docs' examples are compiled and run (doctests).

## Honest results

- **Warnings are errors in CI**: `cargo clippy --workspace --all-targets
  --all-features -- -D warnings` and `RUSTDOCFLAGS="-D warnings" cargo doc`.
- **Formatting is `cargo fmt --check` with the default configuration.** No
  `rustfmt.toml` unless the project records why. (**house**)
- **An allowed lint carries its reason**: `#[expect(lint, reason = "...")]`
  where the MSRV has it (1.81), otherwise `#[allow]` with the reason in a
  comment beside it.
- **A test that cannot run everywhere is `#[ignore = "<reason>"]`** or gated by
  `#[cfg]` on what it needs, and the run reports the count; an early `return`
  that turns a missing prerequisite into a pass is a silent skip.
- **Tests do not depend on the machine**: not its interfaces, locale, time
  zone, home directory or environment.
- No test needs the internet.

## Toolchains and platforms

- **The MSRV leg runs build, tests and clippy**, not only a build: an older
  clippy is not a subset of a newer one, and `incompatible_msrv` does not
  catch every newer `std` API.
- **A change that touches paths, processes, dynamic loading, symbols or
  anything `#[cfg]`-conditional runs on Linux, Windows and macOS** before it
  is called done. Symbol names (`_` prefix on Mach-O), unloading (macOS keeps
  an image mapped) and PIC defaults differ, and one platform passing proves
  nothing about the others.
- Miri on a pinned nightly over every module with `unsafe`
  ([UNSAFE.md](UNSAFE.md)).

## Coverage

Report it; do not gate on it. A threshold rewards tests written to reach
lines rather than to check behaviour.
