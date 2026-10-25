# Python Directives

Python packaging, tooling, CI and release, on top of
`$ENGINEERING_OVERLAY_ROOT/flows/REPO.md` (read that first). How a library is
designed, named, documented and commented is in
`$PYTHON_OVERLAY_ROOT/standards/README.md`; a project's exceptions to it are
in its `.agents/AGENTS.md`.

## Packaging and Layout

- **Source layout**: `src/<package_name>/`, never a flat top-level package (a
  flat one lets `import <package_name>` resolve to the checkout instead of the
  installed artifact).
- **Build backend**: `hatchling` via `pyproject.toml`, no `setup.py`. Literal
  config: `$PYTHON_OVERLAY_ROOT/references/pyproject.toml`.
- **Python version**: a modern floor (`requires-python = ">=3.9"`). Every file
  using bare `X | Y` in a runtime-evaluated position needs
  `from __future__ import annotations`, or the floor breaks.
- **Typing**: ship `src/<package_name>/py.typed`; hatchling includes it with
  the package.
- **Wheel packages**: hatchling finds `src/<package_name>/` by itself only when
  the import name matches the distribution name (`my-lib` → `my_lib`). The
  reference `pyproject.toml` always sets
  `[tool.hatch.build.targets.wheel] packages = ["src/<package_name>"]`, so a
  copy works either way.
- **Shipped docs**: `README.md` (`readme = "README.md"`, the long description)
  and the API header `src/<pkg>/AGENTS.md` (REPO.md's "shipped API header")
  must land in the sdist AND the wheel.
  - The wheel ships `src/<pkg>/` only, so the header lives inside the package
    dir and ships with no configuration. **Never** force-include an
    `AGENTS.md` into `<pkg>/`: beside a repo-root `AGENTS.md` the wheel build
    fails (`A second file is being added to the wheel archive at the same
    path`).
  - The repo-root `AGENTS.md` (contributor orientation) ships in neither: the
    reference manifest excludes `/AGENTS.md` from the sdist.
  - To ship the README inside the installed package too (for
    `importlib.resources`), force-include only it:
    `[tool.hatch.build.targets.wheel.force-include]`
    `"README.md" = "<package_name>/README.md"`.
  - Verify with `python -m build` + `unzip -l dist/*.whl`.
- **Optional dependencies**: zero *required* runtime deps where feasible. One
  extra per integration (`pkg[s3]`, never a catch-all), guarded by
  `try/except ImportError` with a stdlib fallback or clearly degraded
  behaviour, never a crash on import. `dev`/`docs` are tooling, not features.
- **Dependency ranges** (runtime deps and feature extras, in `pyproject.toml`
  only; a constraint restated elsewhere drifts):
  - **pre-1.0**: floor at the minor's `.0`, ceiling at the next minor,
    `>=X.Y.0,<X.(Y+1)`. Raise the floor above `.0` **only** when the code uses
    an API added in a later patch, and name that API and patch in a comment
    beside it;
  - **1.0 and above**: major-scoped, `>=1.0,<2`; a minor floor only when the
    code uses an API added in that minor;
  - **a dependency that ships a major every few months** (cryptography): a
    floor at what the code needs, proved by the `floors` job, and no ceiling,
    with the reason in a comment beside it.
  - "Update to the latest" moves the range to the latest *series*; it does not
    pin the latest patch. The `floors` job of the reference `test.yml`
    installs every declared dependency AT its floor.
- **README badges**: copy the row from
  `$PYTHON_OVERLAY_ROOT/standards/base/DOCS.md`; do not compose one. PyPI
  badges 404 until first publish, which is fine.

## Testing and Development

- **Version policy**: develop on the **latest** stable Python (3.14) and also
  test the **floor** in `requires-python` (3.9). CI runs the full matrix;
  locally, test latest by default and the floor before a release or after a
  significant chunk of work (`dotagents py 3.9 -- -m pytest -q`).
- **Project venvs live in `<project>/.pyvenv/<version>-<os>-<arch>/`**
  (gitignored), one per interpreter, created with **`dotagents pyvenv
  [version]`**, never by hand and never in `.venv/`. No version means the
  latest Python on the machine. Re-running it leaves an existing venv as it
  is. `-g` makes one in `~/.pyvenv/` instead. **Never** install into the
  system or user Python unless told to. A project that keeps its venvs
  elsewhere (its own `.venv/`, uv, poetry) records that in its
  `.agents/AGENTS.md`; follow the record and do not move them.
- **Agents' venvs are not project venvs.** `.agents/.pyvenv/` holds the venvs
  dotagents builds for skills, overlay helpers and setup scripts
  (`$AGENTS_PYTHON`). Never create one, install into one or test a project in
  one.
- **How a venv is named**: `<os>` is `nt`/`posix`/`darwin`; `<arch>` is what
  the interpreter was **built** for (the tail of its
  `sysconfig.get_platform()`: `arm64`, `amd64`, `x86_64`), never
  `platform.machine()`, which reports the host and so files an emulated x64
  CPython on an ARM64 box as native. Both are probed from the interpreter.
  **Native first**: among installs that satisfy the spec, one built for the
  machine's architecture wins before a higher version (an ARM64 3.9.10 over an
  emulated x64 3.9.13), with a warning when only an emulated one fits.
- **Running Python: `dotagents py [version] -- <args>`**. It runs the
  project's `.pyvenv/` venv for that version when one exists, else the user
  one in `~/.pyvenv/` (created when missing); it never creates a project venv.
  Everything after `--` goes to the interpreter, with the real stdio and exit
  code. Use it instead of locating `.pyvenv/<tag>/.../python` by hand; where a
  venv's own executables are needed, they are under `Scripts\` (Windows) or
  `bin/`.
  ```
  dotagents py -- -c "print(1)"       # latest Python on the machine
  dotagents py 3.9 -- -m pytest -q    # the floor
  ```
- **Interpreters**: add them with the Python Manager (`py install <ver>`;
  `py -0p` lists installs with their paths); `dotagents pyvenv` and
  `dotagents py` find them the same way. **Avoid Microsoft Store Python**: its
  app-execution alias sandboxes the filesystem, and `pip install` exits 0
  having installed nothing.
- **Install**: `python -m pip install -e ".[dev,<extras-with-tests>]"` in the
  project venv. Include every extra that has tests, or those tests silently
  skip.
- **Test config**: `pythonpath = ["src"]` + `testpaths` under
  `[tool.pytest.ini_options]` (in the template).
- **Optional-extra tests guard their imports**: the module starts with
  `pytest.importorskip("<sdk_module>")`; a bare import of a missing extra
  breaks *collection for the whole suite*. Verify with `pytest -rs`: "0
  failures" with targeted tests skipped is not verification.
- **Formatting**: **black**, in the `dev` extra, with `[tool.black]
  target-version = ["py<floor>"]` so it never emits syntax the floor cannot
  parse. Format with `black src/ tests/` before committing, then re-run the
  tests. `black --check` is the verify form, run on the newest interpreter
  only: `target-version` already keeps the output parseable by the floor.
- **Linting and type-checking**: none by default (no ruff/mypy/pyright) unless
  the project opts in.
- **`.gitignore` language block** (source for the template placeholder):
  `__pycache__/`, `*.py[cod]`, `.pytest_cache/`, `.hypothesis/`,
  `.mypy_cache/`, `.ruff_cache/`, `.coverage*`, `htmlcov/`, `*.egg-info/`,
  `.pyvenv/`, `.venv/`. Include the **whole** list even for tools not in use
  yet: the dirs appear the first time anyone runs one, and a global
  `core.excludesFile` does not travel with the repo.

## CI/CD: Implementing the Three-Workflow Split

Templates: `$PYTHON_OVERLAY_ROOT/references/workflows/python/{test,release,docs}.yml`
(REPO.md's three-workflow split).

- **`test.yml` is every check the release gate runs**: the test matrix, the
  `floors` job, `build` (sdist + wheel, `twine check`, the `dist` artifact)
  and `wheel-smoke` (the built wheel installed into a clean venv on the oldest
  and newest Python, imported, and checked for `py.typed` and the shipped
  `AGENTS.md`). It runs on `workflow_dispatch` (with a `ref`), a pushed `ci-*`
  tag or `ci-*` branch, and `workflow_call`.
- **`release.yml` calls `test.yml`** (`uses: ./.github/workflows/test.yml`), so
  the release runs the same steps a `ci-*` run does and publishes the `dist`
  that run built. It then checks that the tag names the built version, runs a
  `docs-gate` (`mkdocs build --strict`, no deploy; verify locally first with
  `pip install -e ".[docs]"`), creates the GitHub release, and for a final tag
  publishes to PyPI and dispatches `docs.yml` at the tag.
- **Only `docs.yml` deploys Pages**: on a push to `main` touching docs sources,
  and on `workflow_dispatch`, which the release fires (a release made with
  `GITHUB_TOKEN` starts no workflow, so `release: published` never fires).
- `publish-pypi` uses Trusted Publishing (OIDC, `id-token: write`), never a
  stored token, and `skip-existing: true` so a re-run of a partial release
  skips files already uploaded.
- **One-time owner setup** (confirm before relying on `docs.yml` or
  `publish-pypi`):
  - PyPI Trusted Publishing registered for the project and `release.yml`.
  - Pages source is GitHub Actions: `gh api repos/<gh_org>/<project_name>/pages`
    must show `"build_type": "workflow"` (404 means Pages is off; a branch
    source silently ignores the deploy). Set it with
    `gh api -X POST repos/<gh_org>/<project_name>/pages -f build_type=workflow`
    (`-X PUT` to switch an existing branch source). The other settings are in
    REPO.md.
  - A deploy job that is skipped, not failed, is usually the `github-pages`
    environment's protection rules: read its log.
- **A green run can still be wrong**: a job exits 0 while emitting check-run
  annotations (`mkdocs --strict` nav warnings, `setup-python` cache misses)
  that do not show in the step's tail. Read them with
  `gh api repos/<gh_org>/<project_name>/check-runs/<id>/annotations` (`<id>`
  from `gh run view <run-id> --json jobs`).

## Releases and Versioning

- **PEP 440 vs SemVer**: `pyproject.toml` `version` is PEP 440 (`1.0.0rc1`;
  hatchling and pip reject SemVer pre-release syntax); git tags and
  `CHANGELOG.md` keep SemVer (`v1.0.0-rc.1`). Never "fix" one to match the
  other.
- Bump `version` in the same commit as the changelog entry.
- The bump follows REPO.md "Versioning": pre-1.0, additive API is a PATCH, so
  a changelog with only an `### Added` section is still `0.9.0` → `0.9.1`.
- **Before tagging**, assert:
  - `src/<package_name>/py.typed` exists, the `"Typing :: Typed"` classifier
    is in `pyproject.toml`, and the marker reaches the wheel
    (`python -m build`, then `unzip -l dist/*.whl | grep py.typed`);
  - `license`/`license-files` (PEP 639) are set, with no legacy `License ::`
    classifier beside them.

## Documentation Site

- MkDocs + Material + `mkdocstrings[python]`; literal config:
  `$PYTHON_OVERLAY_ROOT/references/mkdocs.yml`. Plain-prose docstrings render;
  per-parameter tables need `:param:` sections.
- `docs/changelog.md` may snippet-embed `CHANGELOG.md` (`pymdownx.snippets`):
  its links are absolute, unlike a README embed (REPO.md).

## Known Difficulties (symptom → fix)

- Windows venv `WinError 145` / corrupted package (missing submodules, missing
  Material theme files) → venv pip `install --force-reinstall <package>`.
- MkDocs strict-nav errors on repo-level markdown when snippets
  `base_path: ["."]` → set `docs_dir: docs` (the template does).
- Windows/PowerShell venv console script resolving outside the venv →
  `.pyvenv\<tag>\Scripts\python.exe -m <module>` instead of the script.
- pyright reports nothing for a file under a dot-directory (it skips them) →
  type-check a probe outside `.agents/`.
