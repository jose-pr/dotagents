# Standard: type annotations

Base standard: applies to every library that ships `py.typed`. Index:
[README.md](../README.md). Whether a project runs a type checker is its own
decision, per `../../kb/PYTHON.md`; these rules hold either way, and the last
section applies once it does.

## The public API is annotated

- **Every public function, method and attribute has complete annotations**,
  including `*args`, `**kwargs` and return types. A consumer's checker reads
  them through `py.typed`; an unannotated public name is `Any` to them.
- **Annotations are true.** No `# type: ignore` to make a signature that is
  wrong check. An ignore carries the error code and a reason.
- **A parsed document is `Any`; an opaque value is `object`.** `Any` says
  "you may do anything with this"; `object` says "I will not look inside".
- A parameter that defaults to `None` is `Optional`.
- Public aliases — `<Type>Like`, callback types — are exported and listed in
  the shipped header.

## Valid on the oldest supported Python

- **`from __future__ import annotations` in every module**, so annotations
  are not evaluated at import.
- **Syntax newer than the floor never appears where it is evaluated at run
  time**: `X | Y` in an alias, a base class, `isinstance`, a `cast`.
- **A public annotation resolves with `typing.get_type_hints` on the floor.**
  A name used in a hint is importable at run time, not only under
  `TYPE_CHECKING`, wherever a consumer may introspect the signature.
- `typing_extensions` is a declared dependency with a version marker, or it
  is not used.

## Shapes

- **A contract a caller implements is a `typing.Protocol`** or an abstract
  base class, not a comment.
- **A function whose return type depends on an argument has overloads.**
- **Twins have separate signatures.** A sync and an async method never share
  one annotated as returning a value or an awaitable.
- A method returning its own type uses a bound `TypeVar` below 3.11.

## When the project runs a checker

- One checker, named in the project, run in CI on the oldest and the newest
  supported Python.
- For a library: a completeness gate on the public surface — every exported
  symbol typed and documented.
- Where the checker cannot target the floor, the floor is verified by
  running the test suite on it.
