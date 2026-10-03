# Standard: backends and registries

Specialized standard: applies to a library with interchangeable
implementations of one contract — storage backends, file formats, URI
schemes, transports, providers. Adds to the base standards; index:
[README.md](../README.md).

Build this only when there are, or will soon be, two implementations. One
implementation behind a registry is indirection with no return.

## Layout

```text
src/<pkg>/backends/
  __init__.py      the contract, the registry, and the public backend names
  _local.py        one module per backend, private
  _s3.py
```

- **The directory is `backends/`.** Use another word only where the domain
  already has one, such as `schemes/` for URI schemes.
- **One backend per module**, named for the backend. Its public class is
  re-exported from `backends/__init__`.
- **The class is `<Name>Backend`**: `S3Backend`, `YAMLBackend`.

## The contract

- **One base class or `typing.Protocol`**, in `backends/__init__` or
  `backends/_base`, documented method by method: which are required, which
  are optional, what each may raise.
- **Capabilities are derived, not declared twice.** What a backend supports
  follows from what it overrides; there is no parallel list to drift.
- A backend is stateless, or its state and its thread-safety are documented.
  One that holds a resource follows the lifecycle rules of the base
  standard.

## Registration

- **One mechanism per library**: defining the subclass registers it, or an
  explicit `register_<kind>(name, cls)` call does. Not both.
- **Third-party backends register through an entry-point group**,
  `<pkg>.<kind>`, so installing a package is enough.
- **Names are data**: lowercase, stable, documented. Registering a name twice
  is an error, not a silent replacement.
- `names()` lists what is registered; `get(name)` returns the class or raises
  the unsupported error naming what is registered.

## Selection

- An explicit name wins; inference from a file extension or URI scheme comes
  second, by the longest match.
- Where the choice of backend comes from untrusted data, the caller can pass
  an allow-list, and a name outside it is refused like an unknown one.

## Optional dependencies

Each backend's dependency is its own extra, named for the backend, imported
inside the backend's module. A backend whose extra is missing stays
registered and fails when used, naming the extra.
([EXTRAS.md](../base/EXTRAS.md).)

Importing the package imports no backend's dependency.

## Failures

A backend raises the package's own errors. A third-party exception type
never crosses the contract: it is translated, with the backend's name in the
message.

## Contract tests

- **One suite every backend must pass**, parametrised over the registered
  backends.
- **The suite ships** as an importable testing module, so a third-party
  backend runs the same tests the built-in ones do.
- A new backend is not done until it passes that suite unmodified.

## Documentation

The README's feature table has one row per backend: its name, its extra,
what it supports.
