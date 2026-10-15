# Standard: settings and environment variables

Base standard: applies to anything a crate or a binary reads to decide how to
behave. Index: [README.md](../README.md).

## Precedence

One order, everywhere:

1. an explicit argument (or command-line flag);
2. an environment variable;
3. a configuration file;
4. the built-in default.

A higher source overrides a lower one for that setting only. The shipped
header states the order once.

## Environment variables

- **Named `<PROJECT>_<SETTING>`**, upper case, the project's name as the
  prefix: `<PROJECT>_LOG`, `<PROJECT>_PLUGIN_PATH`, `<PROJECT>_LIBRARY`. An
  unprefixed name belongs to nobody and collides with everybody.
- **A variable that is an established convention keeps its name** where the
  crate honours it as specified: `NO_COLOR`, `HTTP_PROXY`,
  `SSL_CERT_FILE`, `XDG_CONFIG_HOME`. A binary's log level is its own
  `<PROJECT>_LOG`, read in `main`. (**house**)
- **Read when the value is needed, in one module**, never in a `static`
  initialiser. A library reads none at all unless its documented purpose
  needs one; it takes the value as an argument and lets the binary read it.
- **A family of switches shares one variable**, not one per crate:
  `<PROJECT>_TRACE=rtsp,vnc` rather than `<PROJECT>_TRACE_RTSP`,
  `<PROJECT>_RTSP_TRACE`, `<PROJECT>_DIAG_INPUT`. (**house**)
- **Booleans accept `1`, `true`, `yes`, `on` and their opposites**, in any
  case; empty means unset. Anything else is an error naming the variable.
- **Opt-in test switches are `<PROJECT>_TEST_<NAME>`**, and the tests they
  enable are `#[ignore = "<reason>"]` by default ([TESTING.md](TESTING.md)).
- **A build-script switch that writes into the source tree is opt-in**:
  `<CRATE>_WRITE_<THING>=1`. A plain `cargo build` never edits tracked files.
- **Every variable is documented** under "Environment variables" in the
  shipped header and in the binary's `--help`: name, effect, values,
  default.
- A crate never calls `std::env::set_var` (it is `unsafe` in the 2024
  edition for good reason) and never changes the working directory.

## Secrets

- A secret comes from an argument, an environment variable or a file named
  by one; never from a command-line argument, which every local user can
  read.
- **A secret appears in no log line, no error, no `Debug` and no `Display`.**
  The type holding it masks it ([VALUES.md](VALUES.md)).
- A schema or configuration field holding one is marked as such (for
  example `writeOnly`, or a crate's own sensitivity flag), and every
  rendering of the configuration honours the mark at every depth, not only
  the top level.

## Configuration files

- **Read through `serde` into typed structs**, with
  `#[serde(deny_unknown_fields)]` on every struct a person writes: a
  misspelled setting must not look like it worked.
- **A missing file is not an error where defaults suffice; a malformed one
  is**, reported with the path and the position, never with the line's
  content ([LOADERS.md](../specialized/LOADERS.md)).
- **A file the program rewrites keeps the person's comments and order**
  (`toml_edit`), and is written whole or not at all: write beside it, then
  rename over it.
- Found by the platform's convention: `$XDG_CONFIG_HOME` (else
  `~/.config`) on Unix, `%APPDATA%` on Windows, overridable by a flag and by
  `<PROJECT>_CONFIG`.

## Tests

- Each setting is tested at every level of the precedence order.
- A test that needs a variable passes it to the code under test as an
  argument or runs a child process with it set; it does not mutate the test
  process's environment.
