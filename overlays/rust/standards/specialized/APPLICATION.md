# Standard: applications and binaries

Specialized standard: applies to a package that builds a binary: a daemon, a
command, a worker another program starts. Adds to the base standards; index:
[README.md](../README.md).

## A library with a `main` in front

- **The logic is in the library**; `src/main.rs` parses arguments, reads the
  environment, configures logging, installs signal handling, calls the
  library and turns its result into an exit status. Everything else is in
  `lib.rs` and its modules, where it is testable without a process.
- **Process-wide concerns belong to `main` alone**: the `tracing` subscriber,
  the async runtime, signal and Ctrl-C handling, the working directory. The
  library below it follows [API.md](../base/API.md), "Process-wide state".
- A helper binary a program starts (a worker) is a `[[bin]]` of the package
  that needs it, looked for beside the running executable, and named in the
  README's Development section.

## Arguments

- **The command line is built with `clap`, using its derive API**
  (`#[derive(Parser)]`, `#[derive(Subcommand)]`, `#[derive(Args)]`).
  (**house**; clap is what cargo, ripgrep and most of the ecosystem use.) No
  hand-written parser, and no second framework. Take only the features the
  command uses (`derive`, plus `env` where flags read the environment);
  `default-features = false` with `std`, `help` and `usage` when the binary's
  size matters.
- **The arguments are a struct, and the struct is the command**: its fields
  are the flags, its doc comments are the help, and `main` hands the parsed
  struct to the library. A flag is not re-read from `std::env::args` anywhere
  else.
- **`--help` lists every flag and every environment variable the program
  reads** ([CONFIG.md](../base/CONFIG.md)): a flag backed by a variable
  declares it with `#[arg(env = "<PROJECT>_<SETTING>")]`, which puts it in the
  help; a variable no flag backs is named in `after_help`.
- **One flag per concept**: a file argument accepts `-` for standard input or
  output; several text formats are one `--format <name>` over a `ValueEnum`.
- Subcommands, when there are several, are one module each under `cli/`, each
  an `Args` struct and a thin call into the library.
- A library that offers a command line as a feature puts it in a `[[bin]]`
  behind a `cli` feature that pulls `clap` in, so a library user never
  compiles it ([FEATURES.md](../base/FEATURES.md)).
- A test calls `Cli::command().debug_assert()`, which checks the whole
  definition (conflicting flags, missing values) in one go.

## Output

- **Results on stdout, diagnostics on stderr**, never mixed.
- **A machine-readable format** behind `--format json`, stable across
  releases, for anything another program may consume.
- No colour when the output is not a terminal or `NO_COLOR` is set.
- **An error is one line on stderr**: what failed and what to do. A
  backtrace appears only with `RUST_BACKTRACE` or a debug flag.
- The log level comes from `<PROJECT>_LOG`, with a documented default.

## Exit status

Documented in the README and the shipped header, and tested:

| Status | Meaning |
| --- | --- |
| 0 | success |
| 1 | the operation failed |
| 2 | the invocation was wrong |
| 130 | interrupted |

`main` returns `ExitCode` (or `Result<(), E>` mapped to one); it does not
call `std::process::exit` past values that need dropping.

## Unattended by default

- **Never prompt** unless standard input is a terminal and the command was not
  told otherwise; every prompt has a flag that answers it. A secret read from
  a terminal is not echoed (`rpassword`).
- **A destructive command needs an explicit flag**, and offers `--dry-run`.
- **Files are written whole or not at all**: write beside the target, then
  rename over it.
- A daemon reloads its configuration by building the new state beside the
  old and swapping, so a configuration that fails to load leaves the running
  one in place.
- A daemon shuts down on the platform's signal, finishes or abandons
  in-flight work as documented, and exits 0.

## Packaging

- A binary that is not published is `publish = false`; one that is follows
  the release workflow like any crate.
- What the program loads at run time (plugin libraries, assets, workers) is
  found relative to the executable or through a documented
  `<PROJECT>_<THING>_PATH`, never relative to the working directory.

## Tests

- Argument vectors go through the real parser (`Cli::try_parse_from`), and
  `Cli::command().debug_assert()` runs once.
- The built binary is run, with real arguments, by an integration test
  (`env!("CARGO_BIN_EXE_<name>")`), and its exit statuses are asserted,
  including for a missing argument.
- Every command in the README runs as written.
