# Standard: optional dependencies

Base standard: applies to every Python library with an extra. Index:
[README.md](../README.md). How extras are declared and version-ranged is in
`../../kb/PYTHON.md`; this file is how they behave at run time.

## Naming

- **One extra per capability**, named for what the user gets: `s3`, `ssh`,
  `yaml`, `cli`. Not for the dependency's name unless the two are the same
  word, and never a mixed bag.
- **`all` exists only as the union of the capability extras**, written by
  reference (`<dist>[s3,ssh,yaml]`), and no capability is reachable only
  through it.
- `dev` and `docs` are tooling, not capabilities. `dev` includes every extra
  that has tests.

## Importing

- **`import <pkg>` works with no extra installed**, and imports no optional
  dependency. One CI job installs the bare package and imports it.
- **Import where it is used**: inside the function or method that needs the
  dependency, or in a private module nothing imports until then. Not at the
  top of a module the package root imports.
- **Catch `ImportError` on the import line only.** A broad `except` there
  hides a real failure inside the dependency.
- Annotations that name an optional dependency's types import them under
  `TYPE_CHECKING`.

## When the extra is missing

- **Fail at the point of use, naming the extra to install**:

  ```text
  S3 paths need the 's3' extra: pip install "<dist>[s3]"
  ```

  Raise `ImportError` when code asked for the capability. When data asked for
  it — a configuration file naming a backend — raise the package's own error
  with the same message.
- **A capability whose extra is missing stays listed.** It is still a known
  name that fails with the message above; it does not vanish from the
  registry and turn into "unknown format".
- **A fallback is documented and visible.** Where the library degrades
  instead of failing, the header says what is lost, and the degraded path is
  logged once at debug.
- Offer a probe — `has_<capability>() -> bool` — where callers need to branch.

## Tests

- A test module for an optional capability starts with
  `pytest.importorskip("<module>")`.
- CI runs with every extra installed and reports its skips; a capability
  silently skipped in CI is untested.
- The missing-extra message is tested: the dependency is hidden, the
  capability is used, the error names the extra.
