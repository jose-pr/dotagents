# Standard: command line of a library

Specialized standard: applies when a command line is a feature of a library,
not the package's reason to exist. Adds to the base standards; index:
[README.md](../README.md).

## Framework

**Build the command line with `duho`.** (**house**) One framework across
libraries means one way to declare arguments, one help format, one set of
logging flags and one completion story.

Use something else only where a recorded decision or an overriding reason
rules `duho` out: the library is one `duho` itself depends on, or it must stay
installable with no third-party package even with its `cli` extra. Record the
exception in the project's `.agents/AGENTS.md`, so the next session does not
"fix" it. A preference for another framework is not a reason. The same rule covers the other preferred libraries:
[base/DEPENDENCIES.md](../base/DEPENDENCIES.md).

## The command is a class

Using `duho` means writing the command as an object, not wrapping a script in
one.

- **Each command is a class.** Its fields are its arguments; `__call__` is the
  command.
- **The work is in `__call__` and in the class's own methods**, which read the
  arguments from `self`. `__call__` shows the flow; a step that needs the
  arguments is a method.
- **`__call__` is not a forwarder.** A body that is one call to a module-level
  function, handing over every field as an argument, is an `argparse` script
  in costume: the function's parameter list repeats the field list, the two
  drift, and nothing was gained from the class.
- **Not everything is a method.** A helper that needs none of the command's
  state stays a plain function, and is easier to test for it.
- **Shared arguments and behaviour are a base class**, not a helper that
  every command remembers to call.
- In a library's command line, the methods call the library's public API. In
  a tool that is only a command, the class is the program.
- Tests build the command from an argument vector (`duho.parse(Command,
  argv)`) and call it; they do not go around it to a function underneath.

## For agents

A `duho` command line is already two more things, with no extra code. Design
for both.

- **A tool server.** With `<NAME>_MCP=stdio` in its environment the program
  serves every command as an MCP tool, with a schema generated from the
  fields. A tool an agent calls many times in a session — a queue, a plan, a
  lookup — is used this way, not as a process per call: there is no start-up
  cost, and arguments are typed instead of quoted through a shell.
- **A self-description.** With `AGENT_HELP=1`, `--help` prints one JSON
  document for the whole command tree. It is complete, and large; ask for it
  once when the whole tree is needed, and for a subcommand's plain `--help`
  when it is not.

What that asks of every command:

- **Field help is written for the reader of a schema**: one line, saying what
  the value is and what omitting it means. It is the tool's documentation.
- **Output is short and stable**: one line for a change, the smallest useful
  part for a read, `--json` where a program will consume it. The output is
  the tool's result, and an agent pays for every line.
- **No prompts.** A command that waits on a terminal hangs a tool call.
- **The exit status and the last line say what happened.**
- **A command that destroys or publishes needs an explicit flag**, so being
  callable as a tool does not make it callable by accident. A root that must
  never be served sets `_mcp_ = False`.
- No secret appears as a field default or in help.

## Where it lives

- **Subcommands: a `cli` package, one module per subcommand.**

  ```text
  src/<pkg>/
    __main__.py        raise SystemExit(main())
    cli/
      __init__.py      the root parser and main()
      _common.py       argument groups and helpers two or more commands share
      serve.py         the `serve` subcommand
      capture.py       the `capture` subcommand
  ```

  The module is named for the subcommand it holds.
- **No subcommands, or commands that are each a few lines: a single
  `cli.py`.** It becomes the package when it gains a subcommand that is more
  than a few lines, or passes about 200 lines.
- **Nowhere else.** Not `main.py`, `_cli.py`, `tools/` or the package
  `__init__`.

## Files stay short

A command module does four things: declare arguments, call the library, format
the result, return an exit code. About 200 lines is the ceiling for one
module. A module that needs more is holding logic that belongs in the library,
where it is public, typed and tested without a parser.

**Nothing is reachable only from the command line.** Every command is a thin
call into an API a library user could make.

## Entry point

- `main(argv: Sequence[str] | None = None) -> int` in `<pkg>.cli`.
- `[project.scripts]` maps the distribution name to `<pkg>.cli:main`.
- `python -m <pkg>` runs the same function through `__main__.py`.

## Dependencies

- **The command line's dependencies, `duho` included, live in a `cli` extra.**
  A library user who never runs the command does not install an
  argument-parsing framework.
- **`import <pkg>` never imports `<pkg>.cli`** or anything only the command
  line needs.
- **Run without the extra, the console script says which extra to install**
  and exits non-zero. It does not raise `ModuleNotFoundError`.
- `<pkg>.cli` is not library API. It is absent from the root `__all__`, and
  the shipped `AGENTS.md` documents it under "Command line".

## Behaviour

- **One flag per concept.** A file argument accepts `-` for stdin or stdout
  through a single `--input` or `--output`; never a separate `--stdin`. A
  command with several text formats has one `--format <fmt>`; never one flag
  per format.
- **Results go to stdout, diagnostics to stderr.** Machine-readable output is
  never mixed with log lines.
- **Exit status**: 0 success, 1 the operation failed, 2 the invocation was
  wrong.
- **Signals are handled here**, not in the library: the command line turns
  Ctrl-C into the library's `shutdown()`.
- **Logging is configured here**, not in the library.

## Tests

- Parse real argument vectors through the real parser. `--help` is not a
  check: argparse exits 0 on it before it reports an unknown flag.
- Every command in the README runs as written.
