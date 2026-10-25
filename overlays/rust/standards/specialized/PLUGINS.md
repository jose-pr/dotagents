# Standard: plugins and providers

Specialized standard: a project that loads providers from shared libraries,
and every crate that ships one. Adds to [FFI.md](FFI.md); index:
[README.md](../README.md).

Build this only when there are, or will soon be, two implementations loaded
by name. One implementation behind a loader is indirection with no return.

## The mechanism

- **Plugins are built on `guatiao`**: a kind is a Rust trait under
  `#[guatiao::kind]`, a provider is a type with `#[derive(Provider)]`, a
  plugin library is one `providers!(..)` call, and the host loads, filters
  and offers through `guatiao`'s `Registry`. (**house**,
  [DEPENDENCIES.md](../base/DEPENDENCIES.md).) No project writes its own
  entry symbol, descriptor or registry.
- **The host is the only code that maps a library**: one module of the
  application, named for it (`plugins`).

## Layout

```text
crates/<project>-api/          the kinds, and the models that cross them
providers/<kind>/<name>/        one provider: an ordinary library
providers/<kind>/<name>/plugin/ that provider as a cdylib: one providers! line
```

- **The kinds live in one dependency-light crate** that a host and every
  provider compile against. It re-exports `guatiao`, and providers name
  `guatiao` only through it (`crate = ::<project>_api::guatiao` on the
  derives), so the two sides cannot drift to different versions.
- **A provider is an ordinary library first**, usable and testable without
  the host: its protocol is plain Rust in its own module, its kind
  implementation a thin layer in `lib.rs`.
- **The entry symbol lives in a `plugin/` crate that is a `cdylib` and
  nothing else.** A feature that gates the entry does not work: Cargo unifies
  features across a build, so two providers linked into one binary would
  both define it ([FEATURES.md](../base/FEATURES.md)).
- **Nothing depends on a `plugin` crate**, except as a dev-dependency of the
  host's tests that is never named in code, to get its library built beside
  the test binary.
- **Dependencies point one way**: a provider depends on the api crate, a
  shared kit and at most a protocol crate; never on the host, the core or
  another provider. Code two providers share is a crate under `crates/`.

## The contract

- **A kind grows by appending.** A new method has a default body and comes
  after every required method; anything else breaks providers already built.
- **A model grows by fields that default** (`Option`, or a declared
  default), never by a required field.
- **A test pins the contract**: each kind's table hash and each model's
  required keys, updated only in a commit that means to break providers.
  Know what the hash covers before relying on it: if it is computed over how
  a signature is spelled, a cosmetic edit to the api crate is a break too.
- **Provider calls are synchronous.** The host calls them from
  `spawn_blocking` or a thread of its own, never on an async worker; only a
  method documented as blocking with a timeout may block.
- **A provider's configuration is validated against its schema before it is
  built**, every time, by the host; reading a configuration is lenient, so
  validation is what refuses a misspelled key.
- **A provider that cannot work here says so as availability**, not as a
  failure the first time it is used.

## The host

- **One registry, kept for as long as anything it offered is alive.** A
  handle taken from a registry points into a mapped library; the host keeps
  the registry (and so the mapping) for the life of every handle, normally
  the process.
- **Configuration reloads build new instances, not new mappings**: a library
  already mapped stays mapped, and its process-wide state carries over to a
  second registry that loads the same file. Document what a reload keeps.
- **A search path is filtered before mapping** (by the library's declared
  kinds), so a file that is not a provider is never run.
- **A failure to load one provider never takes down the others**; the report
  lists what loaded, what was skipped and why, and what failed.
- **Clean the target directory after renaming or removing a provider.** The
  old library is still there, and a loader will load both.

## Tests

- **Every provider of a kind passes one contract suite**, written once beside
  the kind.
- **The host's tests load the real libraries**, built as dev-dependencies,
  from beside the test binary.
- A test proves a provider loaded twice, or a stale library beside a new
  one, is reported rather than silently doubled.

## A plugin for someone else's host

When a third-party program defines the entry symbols (Wireshark, GStreamer,
OBS), `guatiao` does not apply.

- **The runtime crate (the binding to that host) provides one `macro_rules!`
  entry macro** that defines the host's entry symbols. The cdylib that
  expands it owns the plugin's state.
- **The shipped header lists every exported symbol.**
- **A test loads the built library in the real host.**

The two-implementations threshold, "The mechanism", "The contract", "The
host" and "Tests" above do not apply. Of "Layout", only the rule that the
entry symbols live in a crate that is a `cdylib` and nothing else applies.
[FFI.md](FFI.md) applies in full.
