# Standard: wrapping an external command

Specialized standard: applies to a library that runs another program and
uses its result. Adds to the base standards; index: [README.md](../README.md).

## Starting it

- **An argument list, never a shell.** No `shell=True`, no command built by
  joining strings.
- **Find the program once, with `shutil.which`**, and fail before running
  anything when it is absent, with a message naming the program and how to
  get it.
- On Windows, a `.bat` or `.cmd` shim re-parses its arguments through
  `cmd.exe`; refuse one where any argument comes from outside.
- **`stdin` is `subprocess.DEVNULL`** unless input is being fed. A child that
  inherits the caller's standard input can consume it.
- **A secret is never an argument.** Arguments are visible to every local
  user; pass a secret on standard input, in the environment, or in a file.
- Pass an explicit environment where the child's behaviour depends on it;
  add to a copy of the current one, do not replace it wholesale.

## Bounding it

- **Every run has a `timeout`.** There is no call without one.
- On timeout the child and its children are killed, and the library raises
  its own timeout error, a `TimeoutError`.
- The limit is a documented setting, not a literal buried in the call.

## Reading the result

- **Capture both streams; decode explicitly** with a named encoding and
  `errors="replace"`. The platform's default encoding is not the child's.
- **Ask for machine-readable output** where the program offers it, and parse
  that. Where only human text exists, set `LC_ALL=C` so it does not change
  with the user's locale.
- **Check the exit status.** A non-zero exit raises the package's error with
  the program, the status and the last lines of standard error, bounded in
  length.
- **Standard output never appears in an error or a log** when it can carry
  the payload: decrypted data, credentials, file contents.
- State which versions of the program are supported; probe the version where
  behaviour differs.

## Structure

- **One private function runs processes for the whole library**; everything
  else calls it. Timeouts, decoding and error mapping live in one place.
- The async twin uses `asyncio.create_subprocess_exec`, not a thread around
  the blocking call.

## Tests

- **Unit tests put a fake program on `PATH`** in a temporary directory —
  a script that prints, exits non-zero, or hangs — and run the real code
  path. They do not mock `subprocess`.
- Tests cover: the program is missing, it exits non-zero, it times out, it
  prints invalid bytes.
- **Integration tests run the real program** and skip, visibly, when it is
  not installed.
