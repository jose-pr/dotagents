# Standard: `unsafe`

Base standard: applies to every crate. Index: [README.md](../README.md).
What crosses a C boundary is in [FFI.md](../specialized/FFI.md); the traps
behind these rules are in `../../kb/RUST.md`.

## Where it may appear

- **`#![forbid(unsafe_code)]` at every crate root** that needs none, and at
  the top of every module that needs none in a crate that does.
- **`unsafe` lives in named files**, each one there because a boundary needs
  it: the FFI exports, the loader, the raw layer of a data structure, the
  one file of bindings a system library has no safe wrapper for. A crate
  that needs it in one file writes `#![deny(unsafe_code)]` at the root and
  `#[allow(unsafe_code)] mod raw;` on that module only (`forbid` cannot be
  lifted below it, `deny` can). (**house**)
- **A test lists the files allowed to hold `unsafe`** and fails when any
  other file has it, or when a listed file stops needing it. The list is the
  review surface; it stays as narrow as it says it is.
- **The safe layer abstracts both sides of a boundary.** If the crate that
  owns a C ABI only wraps it for callers, every implementer writes its own
  `unsafe` against the raw ABI, and that is where the memory bugs appear.
  The crate that owns the boundary offers the safe way to implement it too.

## The contract

- **A safe function cannot reach undefined behaviour, for any input and any
  sequence of safe calls.** That includes inputs built by safe code from
  public fields, `Default`, `Clone` and other safe constructors. If it can,
  the function is `unsafe fn`, or the inputs that would break it cannot be
  built safely. (Rustonomicon, "How Safe and Unsafe Interact".)
- **A type whose fields carry a safety invariant keeps them private.** A raw
  pointer, a length, a code pointer or a context pointer that a safe method
  later dereferences or calls is not a `pub` field. Construct it through an
  `unsafe` constructor that states the invariant, or through code that
  establishes it.
- **`#[doc(hidden)]` does not make a function unsafe.** A hidden helper that
  generated code calls is still a safe function anyone can call; if it
  trusts its arguments, it is `unsafe fn`, and the macro writes the `unsafe`.
- **Every `unsafe fn` has a `# Safety` section** listing what the caller
  promises. **Every `unsafe` block has a `// SAFETY:` comment** naming what
  makes this call sound, in one or two lines.
- **`unsafe_op_in_unsafe_fn` is on** (the 2024 edition default): an unsafe
  operation inside an `unsafe fn` still gets its own block and comment.
- **Never form a reference to memory you have not proven valid for the whole
  referent.** A foreign struct declared shorter than the Rust type is read
  through raw field projections under its declared size; it is never turned
  into `&T`.
- **Never trust a count to size an allocation.** A length read from foreign
  memory or a file is a bound to check, not a capacity to reserve.
- **A macro never wraps the caller's tokens in `unsafe`.** Bind the caller's
  expression to a typed `let` outside the block, then use the binding inside
  it. (`clippy::macro_metavars_in_unsafe`.)

## Verification

- **Miri runs every module that holds `unsafe`**, in CI, on a pinned nightly,
  with the tests that need no foreign library. A module Miri cannot reach is
  listed with the reason and covered by a native test on each platform.
- Run Miri with Stacked Borrows (the default) and, where a module builds
  self-referential raw structures, Tree Borrows as well; a report under
  either is a defect until shown otherwise.
- A soundness claim in a review is a probe that compiles with
  `#![forbid(unsafe_code)]` and crashes, or a Miri report. "It could" is a
  question, not a finding.
