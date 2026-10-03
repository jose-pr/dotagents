# Standard: tests

Base standard: how tests are designed. Index: [README.md](../README.md).
Running them — virtual environments, the supported-version matrix, the test
configuration — is in `../../kb/PYTHON.md`.

## Layout

```text
tests/
  conftest.py          shared fixtures
  test_<area>.py       one module per source area
  integration/         real sockets, real processes, on loopback
  conformance/         behaviour recorded from a reference implementation
```

A test module imports the package, never another test module.

## What a test proves

- **A test asserts behaviour through the public API**, not how the code is
  arranged. Renaming a private function breaks no test.
- **A test is seen to fail before it is trusted.** Write it, break the code
  it covers, watch it go red. A test that stays green with the feature
  deleted proves nothing.
- **A bug fix arrives with the test that would have caught it**, named for
  the behaviour, not for the bug report.
- **A test does not encode a bug.** When a test and a specification
  disagree, the test is evidence about the code; rewrite it to the
  specification.
- The name states the behaviour; the docstring, where one is needed, says
  what failure it catches.

## Real over mocked

- **Mock what is not yours and cannot be run**: a remote peer, the clock, an
  external binary. Not your own modules.
- The filesystem is real, under the temporary directory the framework gives.
- Time is injected, not slept: wait for a condition with a timeout, never
  `sleep(n)` and hope.
- A network test binds port 0 and reads the port back.
- Where the library talks to something that exists — a protocol peer, a
  reference tool — at least one test talks to the real one.

## Honest results

- **Warnings are errors**: `filterwarnings = ["error"]`, with each exception
  listed and narrow.
- **A skip is conditional on a probe, and visible.** Probe the capability at
  run time; do not skip by platform name. Run with `-rs`, and state the
  expected skip count where a run is called verified.
- **An expected failure is strict** (`xfail(strict=True)`), so the fix that
  makes it pass forces the marker out.
- **Tests do not depend on the machine**: not on its interfaces, locale, time
  zone, home directory or environment, unless a fixture asks for the property
  it needs and skips when the machine lacks it.
- Tests write nowhere outside their temporary directory, and never touch the
  real home.
- No test needs the internet.

## Kinds worth having

- **Property tests** for anything that parses, encodes or round-trips.
- **Contract tests** run against every implementation of a shared interface.
- **The README's and the docs' examples are executed.**
- **The built package is installed into a clean environment and its console
  script run**, on each supported OS.

## Platforms

A change that touches sockets, paths, processes or anything conditional on
the platform runs the suite on Linux and Windows before it is called done.

## Coverage

Report it; do not gate on it. A threshold rewards tests written to reach
lines rather than to check behaviour.
