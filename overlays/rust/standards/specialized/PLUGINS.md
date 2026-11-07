# Standard: plugins and providers

Specialized standard: a project that offers an extension point, and every
crate that ships an extension for one. Adds to [FFI.md](FFI.md); index:
[README.md](../README.md).

An extension point is anything others may add to: handlers, providers,
anything chosen or loaded by name. One the application means to offer is
one from its first, built-in implementation. A choice nobody outside the
project will extend is a trait or an enum; a loader behind it is
indirection with no return. The seam an application embeds the host
through, a command a specification defines and a security rule are not
extension points either.

## The mechanism

- **Every extension point is a `guatiao` kind**: a kind is a Rust trait under
  `#[guatiao::kind]`, a provider is a type with `#[derive(Provider)]`, a
  plugin library is one `providers!(..)` call, and the host loads, filters
  and offers through `guatiao`'s `Registry`. (**house**,
  [DEPENDENCIES.md](../base/DEPENDENCIES.md).) No project writes its own
  entry symbol, descriptor or registry.
- **An extension may be written in any language a guatiao source loads**
  (`guatiao-source-py`, `-js`, `-lua`, `-rhai`, `-wasm`, `-remote`, each
  added with `Registry::add_source_file`): it needs no Rust and no build
  of the host's.
- **A native extension crosses guatiao's C ABI, never a Rust ABI**: a
  kind's table is `repr(C)` and renders to C, so a library built by another
  compiler, `rustc` or language loads. No Rust `dylib`, trait object or
  Rust type crosses a library boundary.
- **The application's built-in implementations are providers of the same
  kind**, registered with `Registry::register_local`, so they are found,
  filtered and configured as a loaded one is, and nothing is reachable only
  because it is built in. A closed enum of implementations that the host
  matches on is an extension point that was not written.
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
- **A provider's configuration is its own settings type**, with the derives
  ([CONFIG.md](../base/CONFIG.md), "Settings as data"), never a text field
  (`settings_toml: String`), whose schema says nothing a host can check.
  Text formats are the host's concern and never cross the kind.
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

The extension-point rule, "The mechanism", "The contract", "The host" and
"Tests" above do not apply. Of "Layout", only the rule that the
entry symbols live in a crate that is a `cdylib` and nothing else applies.
[FFI.md](FFI.md) applies in full.
