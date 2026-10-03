# Standard: conversions

Base standard: one vocabulary for turning a value into and out of text, bytes
and other types. Index: [README.md](../README.md). Value types themselves are
in [VALUES.md](VALUES.md).

## One name per direction

| From | To a value | Back |
| --- | --- | --- |
| human-written text | `Type.parse(text)` | `str(value)` |
| text, without raising | `Type.try_parse(text)` | — |
| another representation | `Type.from_<source>(...)` | `value.to_<target>()` |
| a document or file | `loads(s)`, `load(source)` | `dumps(obj)`, `dump(obj, target)` |
| bytes on the wire | `Type.decode(data)` | `value.encode()`, `bytes(value)` |

- `load`/`dump` are fixed by the loaders standard and `encode`/`decode` by
  the protocol standard. This file adds the first three rows.
- **No synonyms.** Not `from_str`, `from_string`, `to_string`, `serialize`,
  `deserialize`, `marshal`, `as_text`. A second name for the same conversion
  is a second thing to document and to get subtly different.

## `parse` and `try_parse`

- **`parse` is a classmethod taking text and returning the type**; it accepts
  every spelling the type documents. (stdlib `datetime.fromisoformat`,
  `ipaddress.ip_address`.)
- **`parse` raises the package's value error**, a `ValueError`, naming the
  type and what was wrong. An argument of the wrong type raises `TypeError`.
- **`try_parse` returns `None`** — or a `default` the caller passes — for
  text that does not parse, and for nothing else. It still raises
  `TypeError` for an argument that is not text.
- **`str(value)` is canonical**, and `Type.parse(str(value)) == value`.
- The constructor takes the type's own parts or an already-valid value; the
  lenient, many-spellings entry point is `parse`.

## `from_` and `to_`

- **`from_<source>` is a classmethod** building the type from another
  representation: `from_bytes`, `from_dict`, `from_path`. (stdlib
  `int.from_bytes`, `datetime.fromtimestamp`.)
- **`to_<target>` returns a new object** of the other representation:
  `to_bytes`, `to_dict`. `from_x(value.to_x()) == value`.
- A conversion Python has a protocol for uses the protocol: `__str__`,
  `__bytes__`, `__int__`, `__fspath__`, `__iter__`.

## Accepting input

- **`<Type>Like` is the alias for everything a function accepts in place of
  `Type`**: the type itself, its text form, a compatible standard type.
  Functions take `<Type>Like` and return `Type`. (stdlib `os.PathLike`.)
- The alias is public, exported, and listed in the shipped header.

## Tests

- A round-trip property test for each pair above.
- Every documented spelling parses; every rejected input is tested for its
  error type.
