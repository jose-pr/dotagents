# Python Directives

Python-specific extras/overrides on top of the generic repo standard in
`~/.agents/flows/REPO.md` — read that first. Only Python-specific content here.

## Packaging and Layout

- **Source Layout**: `src/<package_name>/` — never a flat top-level package (keeps
  `import <package_name>` from silently resolving to the checkout instead of the
  installed artifact).
- **Build Backend**: `hatchling` via `pyproject.toml` (no `setup.py`). Literal config:
  `~/.agents/references/pyproject.toml`.
- **Python Version**: modern floor (e.g. `requires-python = ">=3.9"`). Add
  `from __future__ import annotations` to every file using bare `X | Y` unions in a
  runtime-evaluated position — omitting it breaks the older end of the range.
- **Typing**: ship `src/<package_name>/py.typed` in the wheel. Hatchling includes it
  automatically for standard `src/` layouts; avoid explicit packages config — it can
  break editable metadata builds.
- **Optional Dependencies**: zero *required* runtime deps where feasible. One extra
  per integration (`pkg[s3]`, never a catch-all bucket), guarded in code by
  `try/except ImportError` with a stdlib fallback or clearly degraded behavior —
  never a hard crash on import. `dev`/`docs` extras are tooling, not features.
- **README badges** (fill the template's badge row): version
  `img.shields.io/pypi/v/<project_name>.svg` → `pypi.org/project/<project_name>/`;
  pythons `img.shields.io/pypi/pyversions/<project_name>.svg`. PyPI badges 404 until
  first publish — fine to include early.

## Testing and Development

- **Virtual Environments**: project-local `.venv/<python-version>/` (e.g.
  `.venv/3.12.10`), gitignored. **Never** install to system/user Python unless
  explicitly told. Create: `python --version` then `python -m venv .venv/<version>`;
  always invoke the venv-scoped executables
  (Windows `.venv\<version>\Scripts\{python,pip,pytest}`, Unix `bin/`).
- **Install**: `.venv/<version>/<Scripts|bin>/pip install -e ".[dev,<extras-with-tests>]"`
  — include every extra that has tests depending on it, or those tests silently skip
  instead of running.
- **Test config**: `pythonpath = ["src"]` + `testpaths` under
  `[tool.pytest.ini_options]` (in the template).
- **Optional-extra tests guard their imports**: the module starts with
  `pytest.importorskip("<sdk_module>")` before importing the code under test — a bare
  top-level import of a missing extra breaks *collection for the whole suite*. Verify
  with `pytest -rs` so skips are visible; "0 failures" with targeted tests skipped is
  not verification.
- **Linting/type-checking**: none by default (no per-push CI, no ruff/mypy) — only if
  the project deliberately opts in.
- **`.gitignore` language block** (source for the template placeholder):
  `__pycache__/`, `*.py[cod]`, `.pytest_cache/`, `.hypothesis/`, `.mypy_cache/`,
  `.ruff_cache/`, `.coverage*`, `htmlcov/`, `*.egg-info/`, `.venv/`.

## CI/CD: Implementing the Two-Workflow Split

Templates: `~/.agents/references/workflows/python/{test,release}.yml`.
- Both install `pip install -e ".[dev,<extras-with-tests>]"` and run `pytest -q`.
- `release.yml`'s docs job gates on `mkdocs build --strict` — verify locally first
  (`pip install -e ".[docs]"`).
- PyPI publish uses Trusted Publishing (OIDC, `id-token: write`), never a stored
  token. PyPI Trusted-Publishing registration and GitHub Pages "Source: GitHub
  Actions" are one-time, owner-only setup on the real repo — confirm they're
  registered before assuming `publish-pypi`/`docs-deploy` will work.

## Releases and Versioning

- **PEP 440 vs SemVer**: `pyproject.toml` `version` must be PEP 440 (`1.0.0rc1`, no
  hyphen — hatchling/pip reject SemVer pre-release syntax); git tags and
  `CHANGELOG.md` keep SemVer (`v1.0.0-rc.1`). Same release, two syntaxes — never
  "fix" one to match the other.
- Bump `version` in the same commit as the changelog entry.

## Documentation Site

- MkDocs + Material + `mkdocstrings[python]`; literal config:
  `~/.agents/references/mkdocs.yml`. Plain-prose docstrings render fine —
  mkdocstrings just won't build per-parameter tables without `:param:` sections.
- `docs/changelog.md` may snippet-embed `CHANGELOG.md` (`pymdownx.snippets`) since its
  only links are absolute URLs — safe where a verbatim README embed isn't (REPO.md).

## Known Difficulties (symptom → fix)

- Windows venv `WinError 145` / corrupted package (missing submodules, missing
  Material theme files) → venv pip `install --force-reinstall <package>`.
- MkDocs strict-nav errors on repo-level markdown when snippets `base_path: ["."]` →
  set `docs_dir: docs` (the template does).
- Windows/PowerShell venv console script resolving outside the venv →
  `.\.venv\<version>\Scripts\python.exe -m <module>` instead of the direct script.
