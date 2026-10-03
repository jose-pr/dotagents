# Standard: applications

Specialized standard: applies to a package whose reason to exist is its
command. A library that merely ships a command line follows
[CLI.md](CLI.md) instead. Adds to the base standards; index:
[README.md](../README.md).

## An application is a library with a command in front

- **Commands live in `cli/`, one module per subcommand**, with the same
  rules as [CLI.md](CLI.md): declare arguments, call into the package,
  format the result, return an exit status. About 200 lines per module.
- **Everything outside `cli/` is library code** and follows the base
  standard: it returns, raises or logs; it does not `print`, call
  `sys.exit` or read `sys.argv`.
- The command line is built with `duho`
  ([DEPENDENCIES.md](../base/DEPENDENCIES.md)). Here it is a required
  dependency, not an extra: the command is the product.
- **Each command is a class with its logic in `__call__` and its methods**,
  never a forwarder to one function, and it is designed to be served to
  agents as a tool as well as run by hand ([CLI.md](CLI.md), "The command is
  a class" and "For agents").
- `main(argv=None) -> int` in `<pkg>.cli`; `python -m <pkg>` runs it.

## Output

- **Results on stdout, diagnostics on stderr.**
- **A machine-readable format** behind `--format`, stable across releases,
  for anything another program may consume.
- No colour when the output is not a terminal or `NO_COLOR` is set.
- **An error is one line on stderr**: what failed and what to do. A
  traceback appears only with `--debug` or `<PKG>_TRACEBACK`.

## Exit status

Documented in the README and the shipped header, and tested:

| Status | Meaning |
| --- | --- |
| 0 | success |
| 1 | the operation failed |
| 2 | the invocation was wrong |
| 130 | interrupted |

A command that answers a yes-or-no question may give its "no" a status of
its own; it says so.

## Unattended by default

- **Never prompt** unless standard input is a terminal and the command was
  not told otherwise. Every prompt has a flag that answers it.
- **A destructive command needs an explicit confirmation flag**, and offers
  `--dry-run` showing what it would do.
- Commands are idempotent where the operation allows: running one twice is
  the same as running it once.
- **Files are written whole or not at all**: write beside the target, then
  replace it.

## Settings

Flags, then environment, then a configuration file, then defaults
([CONFIG.md](../base/CONFIG.md)). The command configures logging; `-v` and
`-q` adjust it.

## Extending

Commands discovered from outside the package follow
[EXTENSIONS.md](EXTENSIONS.md): one mechanism, names that cannot collide
silently, and a failure to load one command never takes down the others.

## Tests

- Argument vectors go through the real parser.
- The built package is installed into a clean environment and its console
  script run on each supported OS.
- Every command in the README runs as written.
- Exit statuses are asserted, including for a missing argument and for
  Ctrl-C.
