# Standard: value types

Base standard: applies to a type that stands for a value — an address, a
name, a version, a duration, an identifier, a parsed token — as opposed to a
thing with a lifecycle. Index: [README.md](../README.md). (`std::net::IpAddr`,
`std::time::Duration`, `semver::Version`, `uuid::Uuid`.)

## A newtype that holds its invariant

- **A value with rules is a newtype**, never a bare `String` or `u32`
  passed around with a comment. Its constructor validates and normalises;
  every instance is valid, and two spellings of one value are equal from the
  moment they exist.
- **Fields are private** (`C-NEWTYPE-HIDE`): a public field lets anyone
  break the invariant the constructor established.
- **A closed set of names is an enum**, not a `String` with a `pattern`. Its
  text form is chosen once (`#[serde(rename_all = "kebab-case")]`, or the
  crate's own equivalent), and an unknown value is an error, or, where the
  set is open by design, a variant that keeps the text (`Other(String)`).
- "Changing" a value returns a new one; a value type has no setters.

## Common traits

(`C-COMMON-TRAITS`.)

- **Always**: `Debug`, `Clone`, `PartialEq`.
- **Wherever they hold**: `Eq` and `Hash` (together, from the same fields);
  `Copy` for a small plain value; `PartialOrd`/`Ord` only where there is one
  natural total order, and consistent with `Eq`; `Default` only where there
  is an obvious empty value.
- **`Send` and `Sync`**, unless the type cannot be; a test asserts it
  (`fn assert_send_sync<T: Send + Sync>() {}`).
- **`Display` and `FromStr`** for a value a person writes
  ([CONVERSIONS.md](CONVERSIONS.md)).
- **`serde::{Serialize, Deserialize}` behind a `serde` feature**
  (`C-SERDE`), deserialising through the validating constructor
  (`#[serde(try_from = "String")]`), never around it.
- A string-like type also implements `PartialEq<str>`, `AsRef<str>` and
  `Borrow<str>` where its `Hash` agrees with `str`'s.

## `Debug`

- **`Debug` shows the value**, as `String` and `Vec` do: the text, the
  number, the bytes. A derived `Debug` that prints a raw pointer, a length
  and a capacity is a defect on a value type.
- **`Debug` never follows a pointer it cannot vouch for**: a value that may
  hold foreign memory prints what it can check (its tag, its length).
- **A value holding a secret prints neither it nor a hash of it**, in `Debug`
  or `Display`. It prints the type and a fixed placeholder.

## Keeping it a value

- **No I/O in construction, comparison, hashing or conversion.** A method
  that reaches the network or the filesystem is named as an action.
- **No interior mutability.** A `Cell` or a cache inside a value makes
  equality and hashing depend on history.

## Tests

- Equal spellings are equal and hash equal; unequal values are unequal.
- `s.parse::<T>()` of `v.to_string()` equals `v`.
- `Debug` of a value with a secret does not contain the secret.
