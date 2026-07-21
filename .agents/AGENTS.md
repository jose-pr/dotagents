# dotagents — repo working notes (private)

This repo is the source of truth for the global agent config. The product is a
`dotagents` CLI (`src/dotagents/`) plus config content: the neutral **base overlay**
(`src/dotagents/_overlay/`, what `init` lays down) and opt-in example **overlays**
(`overlays/<name>/`, applied with `install --overlays <path>`). Required tooling is at
top-level `tools/`. This file governs work *on* the repo; the config's own rules apply
to installed sessions, not to developing it here.

## Always-on rules

- **Public-repo exception to the global Leakage rule**: this repo intentionally
  tracks `.agents/` and the config content (there is no root `AGENTS.md` — D54). Everything tracked is public —
  sanitize: no user accounts, no machine paths, no private repo/plan names.
  `tools/audit_config.py --repo-hygiene .` enforces the mechanical part.
- **Edit here, then reinstall**: change files under `src/dotagents/_overlay/`,
  `overlays/`, or `tools/`, then reinstall to test (`python install.py init`/`install`);
  never edit `~/.agents` directly (it's an install target).
- **Config wording is load-bearing** (token-budgeted): before re-expanding or
  re-adding prose, check the moved-rule mapping and size budgets in
  `.agents/dotagents/NOTES.md`.
- **Before committing**, all must PASS:
  ```
  python tools/audit_config.py --root .
  python tools/audit_config.py --check-templates --root .   # 3.11+
  python tools/audit_config.py --repo-hygiene .
  ```
- **Findings & decisions** go in `.agents/dotagents/` (the config's design log — not a
  plan): one file per decision under `decisions/D<nn>.md`, indexed in
  `.agents/dotagents/DECISIONS.md`, reference prose in `.agents/dotagents/NOTES.md`.
  Continue the D-numbering. Plans about the config live in `.agents/plans/` (tracked —
  written for public reading).

## dotagents CLI package (`src/dotagents/`) — architecture notes

- `install.py` is a thin shim over `dotagents.cli.main()`; the real CLI (built on
  `duho` + `pathlib_next`, both sibling-checkout dependencies pinned in
  `pyproject.toml`) lives under `src/dotagents/`. `dotagents init` writes the
  neutral base overlay `src/dotagents/_overlay/`; `dotagents install` writes the full
  `payload/` (via `--from payload` when run from this repo checkout — a plain
  `pip install` has no bundled full payload and requires `--from`).
- `src/dotagents/_merge.py` implements the managed-block merge for `init`'s
  `AGENTS.md`/`CLAUDE.md` (marker lines `<!-- dotagents:begin -->`/
  `<!-- dotagents:end -->`); `src/dotagents/_sync.py` wraps `pathlib_next`'s
  `PathSyncer` for `install`'s backup/copy/report behavior.
- `src/dotagents/_link.py` backs `dotagents link`/`sync` (D37): symlink (or `--copy`)
  a project's `.agents` to `~/.agents/projects/<name>` and git-sync the private repo.
  Pure-stdlib (`os`/`shutil`/`subprocess`) — no `pathlib_next`, so it works in a plain
  `pip install`. The private-repo model + cloud hooks ship in `overlays/private-sync/`
  (installs `kb/PRIVATE_SYNC.md` + `hooks/`).
- **duho ≥ 0.3.3 API (D38)**: commands are `class X(LoggingArgs, Cmd)` with a `__call__`
  entrypoint (data mixin first, `Cmd`/`Cli` base last); the umbrella is
  `Dotagents(LoggingArgs, Cli)`. NOT the old `LoggingArgs` + `__run__` (0.1.x). A dev
  box with only duho 0.2+ installed will make even `init` fail loud ("not runnable, make
  it a Cmd") if the code still uses `__run__` — check `duho.__version__` before blaming
  the code.
- **Gotcha — duho AST field-introspection breaks in a `.pyz`**: duho reads each field's
  flags + help by parsing its module source via `module.__file__`; inside a zipapp that
  path isn't a real file, and duho's `getclsdef` swallows the `OSError` before its
  `inspect.getsource` fallback — so flags degrade to name-derived (`--from`→`--from-`, a
  positional→`--name`) and help vanishes. `cli._repoint_zipapp_sources()` (called at the
  top of `main`) extracts `dotagents.cli`/`duho.presets` to temp files and repoints
  `__file__` before dispatch; keep positionals/aliased flags covered by the `build-pyz`
  CI smoke so a future duho change can't silently re-break the pyz.
- **Gotcha — pathlib_next needs `typing_extensions` on Python <3.10**: 0.8.0
  falls back `ParamSpec = TypeVar` when `typing.ParamSpec`/`typing_extensions`
  are both absent, and plain `TypeVar` has no `.args`/`.kwargs` — `import
  pathlib_next` then crashes inside `pathlib_next.utils` (`LRU.__call__`).
  `pathlib_next`'s own `pyproject.toml` does not declare `typing_extensions` as
  a conditional dependency for `python_version < '3.10'` (upstream gap). Any
  Python-3.9 venv testing this package needs `pip install typing_extensions`
  added manually until upstream fixes it.
- **Gotcha — package data inside a built `.pyz`**: `Path(__file__)` inside a
  zipapp is not a real filesystem path, so `.exists()` on it is always False.
  `src/dotagents/cli.py`'s `_package_data_dir()` resolves `skeleton/` and the
  bundled `_payload/` via `importlib.resources.files("dotagents")` instead;
  when that returns a zip-backed `Traversable` (running from a `.pyz`), it is
  extracted once to a temp directory for the process lifetime and that real
  path is cached and reused.
- **Gotcha — `Path.write_text(..., newline=...)` needs Python 3.10+**:
  `src/dotagents/_wrappers.py` (writes the `dotagents`/`dotagents.cmd` wrapper
  scripts) uses plain `open(path, "w", newline="")` instead, to keep the
  Python 3.9 floor.
- `PathSyncer.sync()` (from `pathlib_next.utils.sync`) requires both source and
  target be `pathlib_next.Path` instances (e.g. `LocalPath`), not plain
  `pathlib.Path` — mixing them raises inside `FileStat.from_path` (a plain
  `pathlib.Path.stat()` doesn't accept `follow_symlinks=` the way
  `pathlib_next` expects). It also does not auto-create a target's parent
  directory before copying a file into it; callers must `mkdir(parents=True)`
  the immediate parent before calling `sync()` for a fresh top-level entry.
