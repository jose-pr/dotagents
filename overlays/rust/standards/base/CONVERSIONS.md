# Standard: conversions

Base standard: one vocabulary for turning a value into and out of text,
bytes and other types. Index: [README.md](../README.md). Value types
themselves are in [VALUES.md](VALUES.md).

## One way per direction

| From | To a value | Back |
| --- | --- | --- |
| human-written text | `impl FromStr`, called as `text.parse::<T>()` | `impl Display` |
| another type, infallibly | `impl From<U> for T` | `impl From<T> for U` |
| another type, fallibly | `impl TryFrom<U> for T` | `impl TryFrom<T> for U` |
| a borrowed view | `as_<target>(&self) -> &U` | — |
| a new owned value, keeping `self` | `to_<target>(&self) -> U` | — |
| taking `self` apart | `into_<target>(self) -> U` | — |
| a document or a byte stream | [LOADERS.md](../specialized/LOADERS.md) | |
| bytes on the wire | [PROTOCOL.md](../specialized/PROTOCOL.md) | |

- **The standard traits, not inherent methods with the same meaning.** No
  inherent `from_str`, `to_string`, `from_u32` beside the trait that does it.
  (`C-CONV-TRAITS`.)
- **`as_`, `to_` and `into_` mean what `std` means** (`C-CONV`): `as_` is
  free and borrows, `to_` costs and keeps `self`, `into_` consumes.
- **No synonyms**: not `from_string`, `to_str`, `serialize`, `marshal`. A
  second name is a second thing to keep in step.
- **A fallible reader whose target is a parameter is a trait with the type
  parameter on the trait**, so a call site names the target once:
  `TryAsRef<T>`, `TryFrom<&U>`. A method named `try_into` on a crate's own
  trait collides with `std`'s blanket `TryInto` at every call site; do not
  write one.

## `From` and `TryFrom`

- **`From` never fails and never panics on any input it accepts.** A
  conversion that can refuse is `TryFrom`, whatever the refusal costs.
- **A consuming `TryFrom` whose input is costly to rebuild hands the input
  back** as its error, or in it.
- **Implement the direction that does not need the orphan rule's help**, and
  rely on the blanket `Into`/`TryInto`. Never implement `Into` directly.
- **A short constructor and its `From` do not call each other.** One holds the
  logic; the other delegates. Two that delegate to each other recurse
  forever, and it surfaces as a stack overflow far from the cause.
- A blanket `impl<T: Into<X>> From<T> for Y` is tempting and usually
  conflicts with the reflexive `From<T> for T`; write the impls for the
  concrete types, or a macro that writes them.

## `FromStr` and `Display`

- **`Display` is canonical**, and `s.parse::<T>()` of it gives back an equal
  value.
- **`FromStr` accepts every spelling the type documents** and refuses with an
  error naming the type and what was wrong, not quoting the input when it can
  be a secret.
- A type with several text forms picks one for `Display` and exposes the
  others as methods named for the form (`to_rfc3339`).

## Accepting input

- **Take `impl AsRef<str>`, `impl AsRef<Path>` or `impl Into<T>`** where a
  function would otherwise force a caller to convert first. (`C-GENERIC`.)
  Not on a trait object method, and not where it costs a monomorphisation
  in a hot path for no caller.
- **Borrow what you only read** (`&str`, `&[u8]`, `&T`); take ownership only
  of what you keep.

## Tests

- A round-trip test for each pair above: `parse(display(v)) == v`,
  `T::try_from(U::from(v)) == v`.
- Every documented spelling parses; every refused input is tested for its
  error variant.
