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
- **Ship the consumer's docs in the package**: both `README.md` (`readme = "README.md"`
  → long-description) and the root `AGENTS.md` (agent-facing library-interface doc, see
  REPO.md) must land in the built sdist AND wheel. Hatchling puts `README.md` in the
  sdist by default; to ship them as real files inside the installed package (so a
  consuming agent can read them via `importlib.resources` from site-packages),
  force-include into the wheel, e.g.
  `[tool.hatch.build.targets.wheel.force-include]` with
  `"AGENTS.md" = "<package_name>/AGENTS.md"` (and the same for `README.md` if you want it
  importable-adjacent). Verify with `python -m build` + `unzip -l dist/*.whl`.
- **Optional Dependencies**: zero *required* runtime deps where feasible. One extra
  per integration (`pkg[s3]`, never a catch-all bucket), guarded in code by
  `try/except ImportError` with a stdlib fallback or clearly degraded behavior —
  never a hard crash on import. `dev`/`docs` extras are tooling, not features.
- **README badges** (fill the template's badge row): version
  `img.shields.io/pypi/v/<project_name>.svg` → `pypi.org/project/<project_name>/`;
  pythons `img.shields.io/pypi/pyversions/<project_name>.svg`. PyPI badges 404 until
  first publish — fine to include early.

## Testing and Development

- **Version policy**: develop and run on the **latest** stable Python (currently 3.14),
  and also test the **floor** the package declares in `requires-python` (currently 3.9)
  — that catches version-specific breakage (e.g. APIs added after the floor). Keep one
  venv per version you test against, named per the scheme below. CI runs the full matrix;
  locally, at minimum smoke-test latest + floor.
- **Virtual Environments**: project-local, gitignored, named
  `.venv/<ver>-<os>-<arch>[-<task>]` — `<os>` is `nt`/`posix`/`darwin`, `<arch>` from
  `platform.machine().lower()` (e.g. `.venv/3.14-nt-amd64`, or
  `.venv/3.14-nt-amd64-docs` for a task-specific env). One derivation:
  `python -c "import os,platform;print(platform.python_version()+('-nt-' if os.name=='nt' else '-posix-')+platform.machine().lower())"`.
  **Never** install to system/user Python unless explicitly told. Create with the
  chosen interpreter (`py -<ver> -m venv .venv/<tag>` on Windows), then always invoke
  venv-scoped executables (Windows `.venv\<tag>\Scripts\{python,pip,pytest}`, Unix `bin/`).
- **Interpreter management**: use the **Python Manager** (`py install <ver>`;
  `py -0p` lists installs with real paths) to add/select versions — the `py` launcher
  then resolves them. **Avoid Microsoft Store Python**: its app-execution alias
  sandboxes the filesystem and `pip install` silently hangs / no-ops (exits 0 having
  installed nothing, no output past pip's startup line). A pymanager/python.org build
  under a real path (`py -0p` shows one) does not have this problem.
- **Install**: `.venv/<tag>/<Scripts|bin>/pip install -e ".[dev,<extras-with-tests>]"`
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

## CI/CD: Implementing the Three-Workflow Split

Templates: `~/.agents/references/workflows/python/{test,release,docs}.yml` (D52 —
test, release, and docs are three separate workflows; docs deploys on its own so a
release is never the first exercise of the docs build, and the site can be
redeployed without cutting a release).
- test + release both install `pip install -e ".[dev,<extras-with-tests>]"` and run
  `pytest -q`.
- `release.yml` keeps a `docs-gate` job that runs `mkdocs build --strict` but does
  **not** deploy — verify locally first (`pip install -e ".[docs]"`). Only `docs.yml`
  deploys to Pages, on three triggers: `release: published` (a `v*` release ships its
  matching docs — the gated release object, once published, fires this deploy), push
  to `main` touching docs sources (latest between releases), and `workflow_dispatch`.
- `publish-pypi` sets `skip-existing: true` so a re-run of a partial release skips
  files already uploaded (PyPI never replaces a version) instead of hard-failing.
- PyPI publish uses Trusted Publishing (OIDC, `id-token: write`), never a stored token.
- **GitHub Pages preconditions** (one-time, owner-only on the real repo; confirm
  before assuming `docs.yml`/`publish-pypi` will work):
  - PyPI Trusted-Publishing must be registered for the project + workflow.
  - Pages must be set to **Source: GitHub Actions**, not a branch. Check + set via:
    - `gh api repos/<gh_org>/<project_name>/pages` — 404 means Pages is off; the JSON
      `build_type` must read `workflow` (a `legacy`/branch source ignores the Actions
      artifact and the deploy silently no-ops).
    - `gh api -X POST repos/<gh_org>/<project_name>/pages -f build_type=workflow` to
      enable it with the Actions source (use `-X PUT` to switch an existing
      branch-source Pages over to `workflow`).
  - The `github-pages` environment may carry branch/tag protection rules that block a
    deploy from a tag ref — check the deploy job's logs if it's skipped, not failed.
- **Debugging a green-but-wrong run**: a job can exit 0 while emitting check-run
  **annotations** (warnings that never fail the step) — `mkdocs --strict` nav warnings,
  `setup-python` cache misses. They don't show in the step's tail; read them with
  `gh api repos/<gh_org>/<project_name>/check-runs/<id>/annotations` (get `<id>` from
  `gh run view <run-id> --json jobs`).

## Releases and Versioning

- **PEP 440 vs SemVer**: `pyproject.toml` `version` must be PEP 440 (`1.0.0rc1`, no
  hyphen — hatchling/pip reject SemVer pre-release syntax); git tags and
  `CHANGELOG.md` keep SemVer (`v1.0.0-rc.1`). Same release, two syntaxes — never
  "fix" one to match the other.
- Bump `version` in the same commit as the changelog entry.
- **Release-prep checklist** (assert before tagging, not after):
  - `src/<package_name>/py.typed` **exists** (a typed package MUST ship it — an
    absent marker means consumers get no types from the wheel) AND the
    `"Typing :: Typed"` classifier is present in `pyproject.toml`. Verify the marker
    reaches the wheel: `python -m build` then `unzip -l dist/*.whl | grep py.typed`.
  - `license`/`license-files` (PEP 639) set; no legacy `License ::` classifier
    alongside them.

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
  `.\.venv\<tag>\Scripts\python.exe -m <module>` instead of the direct script.
