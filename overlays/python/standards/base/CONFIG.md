# Standard: settings and environment variables

Base standard: applies to anything a library or tool reads to decide how to
behave. Index: [README.md](../README.md).

## Precedence

One order, everywhere:

1. an explicit argument;
2. an environment variable;
3. a configuration file;
4. the built-in default.

A higher source overrides a lower one for that setting only. The shipped
header states the order once.

## Environment variables

- **Named `<PKG>_<SETTING>`**, upper case, the package's name as the prefix.
  An unprefixed name such as `TIMEOUT` or `CREDS` belongs to nobody and
  collides with everybody.
- **A variable that is an established convention keeps its name**: `NO_COLOR`,
  `HTTP_PROXY`, `XDG_CONFIG_HOME`, `SSL_CERT_FILE`. Honour it as specified;
  do not invent a prefixed twin.
- **Read when the value is needed, never at import.** A variable set after
  `import <pkg>` must take effect, and importing must not depend on the
  environment.
- **Read in one module.** Scattered `os.environ.get` calls are how the same
  variable ends up parsed two ways.
- **Booleans accept `1`, `true`, `yes`, `on` and their opposites**, in any
  case; empty means unset. Anything else is an error naming the variable, not
  a silent false.
- **Every variable is documented** under "Environment variables" in the
  shipped header: its name, what it sets, its values, its default.
- A library never writes to `os.environ` and never changes the working
  directory.

## Secrets

- A secret may come from an argument, an environment variable or a file
  named by one. It never comes from, and is never put on, a command line,
  where every local user can read it.
- **A secret appears in no log line, no error message, no `repr` and no
  exception attribute.** The object holding it masks it.
- A variable holding a secret is documented as such.

## Configuration files

- **Found by the platform's convention**: `$XDG_CONFIG_HOME` (else
  `~/.config`) on POSIX, `%APPDATA%` on Windows, overridable by an argument
  and by `<PKG>_CONFIG`.
- **A missing file is not an error; a malformed one is**, reported with the
  path and position, never ignored.
- **Unknown keys are an error**, or a warning naming the key. A misspelled
  setting must not look like it worked.
- Reading follows the loaders standard: explicit encoding, no code execution,
  nothing reached outside the file unless asked.

## Tests

- Each setting is tested at every level of the precedence order.
- Tests set and clear variables through the test framework, never by
  assigning `os.environ` at module level.
- A test asserts that importing the package reads no variable.
