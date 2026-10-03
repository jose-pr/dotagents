# Standard: following a reference implementation

Specialized standard: applies to a library that reimplements something
another implementation defines — a port, a compatible client, a second
implementation of a tool's file format. Adds to the base standards; index:
[README.md](../README.md).

## The reference is the specification

- **Ground truth is the reference implementation at a named version**, not
  its documentation and not memory of it. Where the two disagree, the
  implementation is what users have.
- The README names the reference and the version behaviour is measured
  against.
- **Each module that ports reference code says which upstream files it
  follows**, in its module docstring, so a reader can compare.

## Recorded cases

```text
tests/conformance/
  cases/<area>-<topic>/
    case.<ext>       hand-written: the input and the question
    golden.json      generated: what the reference answered
  record.py          runs the reference; development only
  test_conformance.py   replays the goldens; needs no reference installed
```

- **A case is written by hand; its golden is recorded from the reference and
  never edited.**
- **Replay needs nothing but this library**, so the conformance suite runs
  in ordinary CI on every platform.
- **The golden records what produced it**: the reference's version and the
  environment.
- The recorder has a check mode that re-records in memory and reports drift,
  for when the reference releases.
- Cases cover failures as well as successes: the error, and the exit status
  where a tool has one.

## Differences

Two kinds, kept apart:

| | Meaning | In the tests | In the docs |
| --- | --- | --- | --- |
| **deviation** | deliberate and permanent | asserted as passing | listed in the README |
| **divergence** | a bug still to fix | strict expected failure | not advertised as behaviour |

- **The README has a "Differences from `<reference>`" section**, and a test
  asserts it lists exactly the cases marked as deviations.
- **A divergence is a strict expected failure**, so the fix that closes it
  forces the marker out.
- A reference bug is reproduced, or not reproducing it is a listed
  deviation. It is never silently "fixed".
- **Where the reference is unsafe by default**, following it is allowed only
  with the unsafe path fail-closed and documented; being safer than the
  reference is a deviation and is listed.

## How far fidelity goes

State it once, in the README: results only, or also message text, exit
statuses, output formatting and file layout. Whatever is claimed is covered
by cases.

A coverage table lists the reference's features and which are implemented.

## Ported code

- Code translated from the reference keeps the reference's licence notice in
  the file that holds it.
- The repository carries a `NOTICE` crediting every upstream and the licence
  texts they require; the licences are checked for compatibility before the
  first port, not at release.
