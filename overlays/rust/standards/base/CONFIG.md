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

## Settings as data

- **A builder's settings are also one plain data type** (`TlsSettings`),
  `Default` where every setting has one, which the builder accepts whole
  (`X::builder(..).settings(s)`) beside its setters
  ([API.md](API.md), "Constructors"). A file, a form or a peer fills that
  type; no source needs glue of its own.
- **The type is described once, by `guatiao`'s derives**:
  `#[derive(ToValue, FromValue, Schema)]` (feature `derive`), `#[map(..)]`
  for keys and defaults, `#[schema(..)]` for bounds, sections and
  `sensitive`. Everything else comes from that one description:

| Need | From |
| --- | --- |
| parsing JSON, TOML, YAML | `guatiao_serde::text::<format>::from_str_as::<T>`; any serde format through `from_deserializer` |
| validation | `guatiao::schema::validate_map` against `T::schema()`, refusing what reading refuses |
| defaults | `#[map(default)]` or `#[map(default = <expr>)]`, read and written into the schema's `default` |
| docs | each field's `///` comment, as the schema's `description` |
| a form | `guatiao_intake::form_for`, or `#[derive(Form)]` for sections and widgets |

- **No second description**: no `#[serde(..)]` set beside `#[map(..)]`, no
  hand-written JSON Schema, no doc table restating the fields. A field of a
  serde type crosses through `guatiao_serde::Serde<T>` or
  `#[map(with = guatiao_serde::adapt::any)]`.
- **Exceptions.** A crate whose default build takes no dependency derives
  behind an optional `guatiao` feature, as `C-SERDE` puts serde behind
  `serde`. A crate that must take none, even optionally, keeps a plain
  struct with `Default`, described once on the consumer's side. A side in a
  language with no guatiao binding yet keeps one copy, checked by a test
  against vectors the owning type writes.

## Non-default settings

A default is the safe choice; choosing otherwise is configuration, not an
exception.

- **A library reports every setting looser than its default as data**:
  `non_defaults() -> Vec<NonDefault>`, each `NonDefault { setting, value,
  default }`, on the builder and on what it built. (**house**) Where the
  settings are a described type, the list comes from the schema's
  `default`, not from a comparison kept by hand.
- **It logs one at `debug` at most, never `warn`**: only the application
  knows whether it was asked for
  ([APPLICATION.md](../specialized/APPLICATION.md), "Non-default settings").
- **A setting the platform cannot honour is never silently ignored**: it is
  an error when it is stricter than the default, else reported as not
  applied (`not_applied()`, **house**), so configured-and-applied and
  configured-and-not are told apart.
- **A guard that refuses or forces a setting stays only where it protects
  something concrete**, named in its doc comment: another local user taking
  over a socket, a password crossing the network in the clear. One that
  protects nothing is a plain option. Refusing a meaningless value (a zero
  timeout) is validation, not a guard.
- **No "weaken" in public names, docs, logs or UI**: a setting is default or
  non-default.

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
- A schema or configuration field holding one is marked as such
  (`#[schema(sensitive)]`, which writes `x-sensitive` and `writeOnly`), and every
  rendering of the configuration honours the mark at every depth, not only
  the top level.

## Configuration files

- **Read into the settings type** (above), refusing unknown keys on every
  struct a person writes (`#[map(deny_unknown_fields)]`, or
  `#[serde(deny_unknown_fields)]` in a crate that is serde alone): a
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
- Each looser setting appears in `non_defaults()`, and the defaults report
  none.
- A test that needs a variable passes it to the code under test as an
  argument or runs a child process with it set; it does not mutate the test
  process's environment.
