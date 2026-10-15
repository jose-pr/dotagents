# Standard: formats, parsers and loaders

Specialized standard: applies to a crate that turns text or files into data:
a format bridge, a configuration loader, a path or expression grammar. Adds
to [the base API standard](../base/API.md); index:
[README.md](../README.md). A wire protocol's `encode`/`decode` is in
[PROTOCOL.md](PROTOCOL.md).

## Vocabulary

`serde_json`'s names, with its meanings. (`serde_json`, `toml`,
`serde-saphyr`.)

| Name | Takes | Does |
| --- | --- | --- |
| `from_str(&str)` | content | parses; touches no file, network or environment |
| `from_slice(&[u8])` | content as bytes | decodes, then parses |
| `from_reader(impl Read)` | a stream | reads, then parses |
| `to_string(&T)` | data | returns `String` |
| `to_vec(&T)` | data | returns `Vec<u8>` |
| `to_writer(impl Write, &T)` | data and a sink | writes what `to_string` returns |

- A **grammar** whose result is a typed tree (a path, a filter, a pattern)
  is `FromStr` on its type, or a `parse(&str)` function in its module; it does
  no I/O.
- Opening a path is the caller's job, or a separate `load(path)` that reads
  the file and calls `from_slice`.
- A writer serialises completely before it touches the sink's target file,
  so a value that cannot be written leaves an existing file as it was.

## Input

- **Text is strict UTF-8**; one leading byte-order mark is ignored.
- **Depth is bounded** for every input a caller does not control: by the
  parser's own limit where it has one, and by the crate's depth constant on
  every walk the crate writes itself. A recursive walk over a document is a
  stack a sender controls.
- **Size is bounded** where an input can expand: aliases, anchors, includes,
  entities.

## Result

- **The result keeps the document's order**: a format dependency that sorts
  by default is configured not to (`toml/preserve_order`), and a test
  asserts order, not only lookup.
- **Everything the format can express becomes plain data**: a TOML datetime,
  a YAML tag or a JSON number too large for `f64` arrives as text or a typed
  value, never as a map under a parser's private key.
- **The shipped header states the dialect**, because parsers of "the same"
  format disagree: what a duplicate key does (per format, if the crate reads
  several), how a number that does not fit is kept, whether `NaN` and
  infinities are accepted, how deep a document may nest.

## Failures

- **One parse-error type**, with `line`, `column` (1-based) or `offset`, and a
  message saying what was expected.
- **Never the content.** The message, the fields, the `Debug` output and the
  source chain carry no text from the document, which may hold secrets. A
  parser library's error that quotes the source (a snippet, a caret line) is
  translated: keep its message and position, drop the excerpt.
- **The underlying parser is not part of the API**: swapping it does not
  change what a caller matches.
- **For any input, the parser returns a value or its error.** A panic, a stack
  overflow, or an allocation sized from the input is a defect.
- **Distinct variants for distinct stages**: could not read (an `io::Error`),
  could not parse, parsed but the wrong shape.

## Reaching outside the document

- **Loading never executes code** and never expands environment variables,
  includes, commands or URLs unless the caller turns each on by name.
- **A form the loader does not implement is refused**, not passed through.

## A grammar that round-trips

- **One spelling means one thing.** The parser refuses what the grammar does
  not define, rather than accepting a near-miss that the inverse operation
  then cannot reproduce.
- **Every operation over the parsed form agrees with the parser**: the
  function that lists keys, the one that splits them and the one that writes
  them use the same rule.
- Round trips hold: `parse(display(p)) == p`, and `unflatten(flatten(v)) ==
  v` for every value the forward direction accepts.

## Tests

- Every syntax-error path asserts the variant and the position.
- `from_str(&to_string(&x)?)? == x` for every value the writer accepts, in
  every format.
- Randomised or property tests feed arbitrary text and accept only a value or
  the parse error.
- A test plants a secret-looking value in a malformed document and asserts it
  appears in no message, field or `Debug` output.
