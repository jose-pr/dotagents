# Standard: parsers and loaders

Specialized standard: applies to a library that turns text or files into data
— configuration loaders, data-file backends, expression parsers. Adds to
[the base API standard](../base/API.md); index: [README.md](../README.md).

A wire protocol's `encode`/`decode` is in [PROTOCOL.md](PROTOCOL.md), not here.

## Vocabulary

The `json` module's four names, with its meanings. (stdlib `json`, `tomllib`,
`plistlib`, `pickle`.)

| Name | Takes | Does |
| --- | --- | --- |
| `loads(s)` | `str` or `bytes` content | parses; touches no file, network or environment |
| `load(source)` | an open file, or a path | reads, decodes, then calls `loads` |
| `dumps(obj)` | data | returns `str` |
| `dump(obj, target)` | data and an open file or a path | writes what `dumps` returns |

- **`loads` takes content, `load` takes a location.** A `str` given to `load`
  is a path, never content. No `load_*` function takes text.
- **`parse(text)` is for a grammar** whose result is a tree or a typed object,
  not plain data: a type expression, a filter, a pattern. It never does I/O.
  A value type parses itself with a `parse` classmethod. (stdlib `ast.parse`.)
- `dump` serializes completely before it opens the target, so a value that
  cannot be written leaves an existing file untouched.

## Input

- **Bytes are decoded as strict UTF-8** unless `encoding=` says otherwise. A
  loader never opens a text file with the platform's default encoding.
- **One leading byte-order mark is ignored.**
- A decoding failure is a parse failure (below), reported by byte offset.

## Result

- **`loads` returns plain data**: `dict`, `list`, `str`, `int`, `float`,
  `bool`, `None`, in document order. Building a typed model from that data is
  a separate, named step.
- **The shipped header states the dialect**, because parsers of "the same"
  format disagree: what a duplicate key does; how an unquoted scalar becomes a
  number, boolean, date or null; whether `NaN` and infinities are accepted;
  whether comments are allowed.
- **A duplicate key is an error** in a format this library defines. Where the
  format is defined elsewhere, follow its reference implementation and say so.

## Failures

One parse-error class per package: `(Base, ValueError)`, per the base
standard. It is what every malformed document raises.

- **Attributes**: `path` (the source's name, or `None`), `lineno` and `colno`
  (1-based, `None` when unknown), `msg`. (The names `json.JSONDecodeError` and
  `tomllib.TOMLDecodeError` use.)
- **One line of text**: source, position, problem — `app.yaml:12:5: expected
  ',' or ']'`. No source snippet, no caret, no quoted value.
- **Never the content.** The message, the attributes, the log line and the
  exception chain carry no text from the document, which may hold secrets. A
  parser library's own error often does — a snippet in its message, the whole
  document on an attribute — so it is translated and raised `from None`.
- **The underlying parser is not part of the API.** A caller catches this
  package's error, never the third-party library's; swapping the parser must
  not change what a caller catches.
- **For any input, `loads` returns or raises the parse error.** A bare
  `KeyError`, `IndexError`, `AttributeError` or `RecursionError` escaping
  from malformed input is a defect.
- **Distinct types for distinct stages**: cannot read (`OSError`, let
  through), cannot parse (the parse error), parsed but the wrong shape
  (a validation or configuration error), no backend for this format
  (an unsupported-format error naming the registered ones).
- **A wrong argument is not a parse failure**: an unknown keyword or an
  unusable source type raises `TypeError`. An option no backend reads is
  never silently ignored.

## Reaching outside the document

Loading data must not be able to do anything else.

- **Loading never executes code**: no unsafe YAML loader, no `pickle`, no
  `eval`.
- **Anything that reaches beyond the document is off unless the caller turns
  it on**: include directives, running commands, fetching URLs, reading
  environment variables, rendering templates, decrypting. Each has its own
  switch.
- **Includes are confined** to roots the caller names, checked before the
  file is opened, with depth bounded and cycles detected.
- **Bounds on what a document can cost**: nesting depth, alias or entity
  expansion, interpolation recursion.
- **A command a loader runs** has a timeout, no shell, and its standard
  output — the payload — never appears in an error.
- **Fail closed**: a form the loader does not implement raises; it is not
  passed through to a parser library that might honour it.

## Choosing the format

- By explicit name first, then by file name. The longest matching suffix
  wins; whether matching is case-sensitive is stated.
- Content is not sniffed to guess a format.
- An unknown format raises the unsupported-format error.
- A format whose optional dependency is missing fails with the name of the
  extra to install, when the format is asked for — not at import, and not by
  quietly disappearing from the list.

## Laziness and caching

- Constructing a loader reads nothing; the first use that needs a file reads
  it, and that is where its errors surface.
- A cached parse is revalidated on each use by `(inode, mtime_ns, size)`.
- A cache hands out copies, so a caller mutating a result cannot corrupt it.

## Strictness

One `strict` switch. Tolerated-but-wrong input is logged as a warning naming
the source and line, and with `strict` on it raises instead. Input is never
dropped silently.

## Stages after parsing

Interpolation, includes and merging run after parsing, as their own stage,
with their own error types. Interpolation has a recursion bound and detects
cycles.

## Following a reference implementation

Where the format is whatever another implementation accepts, that
implementation is the specification:

- behaviour is recorded from it as test cases and replayed without it;
- every deliberate difference is listed in the README and asserted by a test;
- a known difference still to be fixed is a strict expected failure, so
  fixing it forces the marker out.

A rule above that the reference implementation contradicts yields to it, and
the difference is recorded as the project's exception.

## Tests

- Every syntax-error path asserts the type, `lineno` and `colno`.
- `loads(dumps(x)) == x` for every value `dumps` accepts.
- A property test feeds arbitrary text to `loads` and accepts only a result
  or the parse error.
- A test plants a secret-looking value in a malformed document and asserts it
  appears in no message, attribute or log record.
