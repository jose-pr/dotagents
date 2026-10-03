# Standard: value types

Base standard: applies to a type that stands for a value — an address, a
name, a version, a duration, a parsed token — as opposed to a thing with a
lifecycle. Index: [README.md](../README.md). (stdlib `ipaddress`,
`pathlib.PurePath`, `datetime`, `uuid.UUID`, `fractions.Fraction`.)

## Immutable

- **A value never changes after construction.** A frozen dataclass, a
  `NamedTuple`, or a class with `__slots__` and read-only properties.
  "Changing" a value returns a new one.
- **Validate and normalise in the constructor.** There is one internal form;
  two spellings of the same value are equal from the moment they exist.
- A constructor that rejects its input raises the package's value error, a
  `ValueError`.

## Equality and hashing

- **`__eq__` and `__hash__` are defined together**, from the same normalised
  form. Defining `__eq__` alone makes the type unhashable and silently
  unusable as a dictionary key or set member.
- **Equal values hash equal.**
- **Comparison with another type returns `NotImplemented`**, never `False`
  and never an exception. A value equals its text form only if the type says
  so deliberately and documents it.

## Ordering

- Define ordering only where the values have one natural total order.
- Define all of it: the four comparison methods, or
  `functools.total_ordering` over `__lt__`.
- Ordering agrees with equality: neither `a < b` nor `b < a` means `a == b`.

## Text forms

- **`str(value)` is the canonical text** a user would write and `parse`
  accepts. ([CONVERSIONS.md](CONVERSIONS.md).)
- **`repr(value)` is unambiguous**: the type's name and the canonical text —
  `MACAddress('00-11-22-33-44-55')`. A default object repr on a value type is
  a defect.
- A value that holds a secret shows neither in `str` nor in `repr`.

## Keeping it a value

- **No I/O in construction, comparison, hashing or conversion.** A method
  that reaches the network or the filesystem is named as an action and
  documented as one.
- **Do not subclass a builtin to carry meaning.** A `str` subclass compares,
  hashes and concatenates as a plain string, so its invariants do not survive
  ordinary use.
- Values copy and pickle without help.
- `__slots__` where many instances live at once.

## Tests

- Equal spellings are equal and hash equal; unequal values are unequal.
- `Type.parse(str(v)) == v` and `v == eval(repr(v))` where the repr is a
  constructor call.
- Comparison with an unrelated type neither raises nor returns equal.
