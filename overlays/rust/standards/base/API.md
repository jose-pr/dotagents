# Standard: public API

Base standard: applies to every Rust crate. Index: [README.md](../README.md).
`unsafe` APIs are in [UNSAFE.md](UNSAFE.md); conversions in
[CONVERSIONS.md](CONVERSIONS.md).

## Names

- **Package, library and repository share one name**, kebab-case for the
  package and the repository, the same with `_` for the library. A
  workspace's crates are `<project>-<role>` (`<project>-core`,
  `<project>-server`); a crate holding one provider is
  `<project>-<kind>-<name>`. (**house**)
- **`C-CASE`: an acronym is one word.** `HttpClient`, `Uuid`, `RtspMessage`,
  `TlsConfig`; never `HTTPClient`. This is the opposite of the Python
  standard and is what `std` does (`TcpStream`, `Ipv4Addr`).
- **A generic noun is used through its module.** `rtsp::Client`,
  `schema::Error`, `io::Error`: the module says what it belongs to, and the
  name does not repeat it. Re-exported at the root, a generic name takes the
  subject only where two would collide. (`std::io`, `std::fmt`.)
- **Methods follow the `std` vocabulary** (`C-CONV`, `C-GETTER`, `C-ITER`):
  a getter is the field's name, not `get_x`; `iter`, `iter_mut`,
  `into_iter`; `len` and `is_empty` together; `with_capacity`; `_mut` for the
  mutable twin.
- **One word stays one word**: `Opcode`, `Hostname`, not `OpCode`.
- No type is named `Error` unless it is the module's error type, and none
  takes the name of a prelude item (`Result`, `Option`, `Box`, `String`).

## Public surface

- **Private by default.** An item is `pub` because a caller needs it; within
  the crate, `pub(crate)`. A module is public only when its path is a
  promise.
- **The root re-exports what the common task needs**: the main types, the
  error, the traits a caller implements or calls through. A specialised
  family stays in its module. Each item has one documented home; the root
  re-exports it, never a wrapper or a newtype made for naming.
- **Every type in a public signature is nameable from a public path.** A
  parameter type that only exists under a private module, or only at a
  deeper path than the function that takes it, is a defect.
- **`#[doc(hidden)]` is for what generated code calls**, and its name starts
  with `__`. A human is never asked to call it, and it is still bound by
  [UNSAFE.md](UNSAFE.md): hidden is not unsafe.
- **Internal code imports from the module that owns an item**, never from a
  root re-export, so moving a module cannot create a cycle.
- **The public surface is committed** where a change to it would go
  unnoticed: `cargo public-api` snapshots per crate, checked in CI, updated
  in the commit that changes the API.

### Module size

A module stays small enough to review in one sitting, a few hundred lines.
A large type gets a directory, one file per concern, with the type's
`impl` beside it. No `utils.rs`, `helpers.rs`, `common.rs` or `misc.rs`:
name a module for what it owns.

## Operations live on their type

- **A function that operates on a type is a method of that type**, with the
  name `std` would give it. A free `thing_set(&mut Thing, ..)` is a C header
  written in Rust. (**house**)
- **The standard traits do the standard jobs**: `From`, `TryFrom`,
  `FromStr`, `Display`, `Default`, `PartialEq`, `IntoIterator`, `Extend`,
  `FromIterator`, `Index`. A bespoke method doing what a trait does is a
  second name to document.
- **No two spellings of one operation.** A short form and its `From` impl do
  not delegate to each other: one holds the logic, the other calls it.

## Errors

- **One error type per area**, `#[non_exhaustive]`, implementing `Display`,
  `std::error::Error` (with `source()` where it wraps) and `From` for the
  errors it wraps. (`C-GOOD-ERR`.) A large crate has one per public module;
  a small one has one.
- **`Display` is one lowercase clause, no trailing period**, and names what
  failed. It never quotes the input: a configuration value, a document line
  or a key may be a secret. Position (line, column, offset, path) is fine.
- **A caller's mistake and a runtime failure are distinguishable by
  variant**, and an error that hands ownership back says so in its type: a
  consuming `TryFrom<T>` whose input is costly to rebuild returns the input
  as the error (`TryFrom<Value> for Map { type Error = Value }`).
- **`Result<T, String>` and `Box<dyn Error>` are not public error types.**
  `anyhow`/`eyre` belong in binaries, never in a library signature.
- **A dependency's error does not cross the API.** It is wrapped as a
  variant or translated; swapping the dependency must not change what a
  caller matches.
- **A library does not panic on input.** `unwrap`/`expect` mark a broken
  invariant, with a message that names it. A function that can panic says
  when under `# Panics`.
- `thiserror` derives the boilerplate where the crate already takes
  dependencies; a crate whose default build takes none writes the impls by
  hand. (**house**)

## Enums and structs that grow

- **A public enum that may gain a variant is `#[non_exhaustive]`**: errors,
  statuses, kinds, modes, reasons. An enum whose variants are the whole
  domain (a `bool`-like choice) is not.
- **A public struct with public fields that may gain one is
  `#[non_exhaustive]`**, or its fields are private behind a constructor.
  (`C-STRUCT-PRIVATE`.) A `#[repr(C)]` struct versioned by its own size is
  the exception, and [FFI.md](../specialized/FFI.md) covers it.
- `#[must_use]` on builders, on types that do nothing unless consumed, and on
  functions whose only effect is their return value.

## Constructors

- **`new` is infallible and cheap**; a fallible constructor returns `Result`
  and is `try_new` or named for its input (`from_parts`, `parse`).
- **Three or more optional settings make a builder**, never a positional
  list. A constructor taking ten arguments, several of them `Option`, is a
  builder that was not written; and a builder lets a later setting default
  without breaking callers.
- **A variant that takes one extra resource is the `_in` or `with_` form**
  (`new_in(alloc)`, `with_capacity(n)`), and the extra argument goes in the
  same position across the crate. (**house**: last, as `std`'s allocator API
  does.)
- **A constructor performs no I/O.** Opening, connecting and loading are
  named actions (`open`, `connect`, `load`), each documented with what it
  touches.

## Resources

- **Ownership is the lifecycle.** A resource is released in `Drop`; a
  release that can fail worth reporting is also an explicit `close(self) ->
  Result<(), E>`, after which `Drop` does nothing.
- **`Drop` never panics and never blocks indefinitely.**
- **A handle that can outlive its owner stays valid**, or its lifetime ties
  it to the owner. A `'static`, `Copy` or `Send` handle into something a
  `Drop` can free is unsound: either the owner keeps the resource alive past
  its own drop, or freeing it is an `unsafe fn` with the contract stated.
- **State who owns what** a function takes: borrowed for the call, moved in,
  or kept. A type that keeps a borrow carries the lifetime.

## Synchronous and asynchronous

- **A library is synchronous unless its purpose is I/O concurrency.** A
  synchronous contract is called from async code through
  `spawn_blocking`; it never blocks an async worker. (**house**)
- **A library never starts a runtime it does not own**, and never assumes
  one: no `Handle::current()`, no `block_on` on a caller's thread. Where a
  sync API must drive async work, it owns a runtime privately and says so.
- **An async API states its runtime.** A crate that needs `tokio` names it as
  a dependency with only the features it uses, and is not generic over
  runtimes in name only.
- **Handles are `Send` when they can be**, and a test proves it by holding
  one across an `.await` (a parameter held across an await does not prove
  it: take the handle *from* an `.await`).
- **Every spawned task or thread has an owner** that joins it and sees its
  error or panic.

## `core`, `alloc`, `std`

- **A library names an item by the lowest crate that has it**: `core::`
  first, then `alloc::` (with `extern crate alloc;` at the root), and `std::`
  only for what needs the operating system: files, processes, threads,
  dynamic loading, `catch_unwind`, the environment. A `std::` path left in
  the source then marks exactly what a `no_std` build would have to give up.
  (**house**)
- **The lints hold it**: `#![warn(clippy::std_instead_of_core,
  clippy::std_instead_of_alloc, clippy::alloc_instead_of_core)]` at every
  library root, made errors by CI's `-D warnings`.
- **Generated code follows the same rule**: a derive or `macro_rules!` emits
  `::core::` paths; an `alloc` item it needs is reached through a
  `#[doc(hidden)]` re-export in the crate that owns the macro, because the
  consumer's crate may not declare `extern crate alloc`.
- `impl core::error::Error` (stable since 1.81), `core::fmt`, `core::ptr`,
  `core::mem`, `alloc::string::String`, `alloc::vec::Vec`.

## Process-wide state

A library changes nothing outside its own values:

- **no `static` state**: no global registry, interner, cache or allocator
  singleton in a crate that may be linked twice into one process (a host
  and its plugins). A test scans the source for `static`, `OnceLock`,
  `LazyLock` and `thread_local!` and allows each exception by name.
  (**house**)
- no logger or subscriber installed; a library emits through `tracing` (or
  `log`) and the binary configures it;
- no signal handlers, no change to the working directory or the
  environment;
- no `println!`, `eprintln!`, `std::process::exit` or `abort` outside a test
  or a binary's `main`. A library returns, errors or logs.

An application may keep process-wide state; it documents it at the
declaration.
