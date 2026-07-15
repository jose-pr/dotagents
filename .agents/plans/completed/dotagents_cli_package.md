# dotagents CLI package + downloadable pyz

Status: done
Executor: Sonnet 5 (`claude-sonnet-5`), effort xhigh, adaptive thinking — coding +
agentic work with judgment calls (merge-branch design, duho arg surface, PathSyncer
hook timing, pip-target/zipapp vendoring). Not Haiku (would guess merge branches);
not Opus (needing Opus to *execute* would mean the plan wasn't ready).

Turn the repo's single `install.py` into an installable Python package `dotagents`
exposing a `duho`-based CLI (`init` / `install` / `build-pyz` / `audit`), plus a
self-contained downloadable `dotagents.pyz`. Built on `duho` (CLI) and
`pathlib_next` (`UriPath` sources + `PathSyncer` sync) so copy/sync/URI logic is
not reimplemented. Adds a **minimal neutral skeleton** distinct from the author's
opinionated payload, so `init` never imposes flows.

## Progress
All phases executed in worktree agent-a8be6941dda4bcc1f, verified, merged to main
2026-07-14. Post-merge: skeleton renamed `_overlay`, design log split to the memory
pattern (`DECISIONS.md` + `decisions/`) — see `design_log_split_and_cli_merge.md`.
- [x] Phase 1 — Package skeleton + pyproject
- [x] Phase 2 — Minimal neutral base overlay (`src/dotagents/_overlay/`)
- [x] Phase 3 — CLI: `init` / `install` / `audit` subcommands
- [x] Phase 4 — `build-pyz` (vendor via pip --target + zipapp) and wrapper install
- [x] Phase 5 — Replace root `install.py`, docs, CI, hygiene
- [x] Verification — all audits PASS (the `pathlib_next` repo-hygiene blocker was a
  PyPI-name false positive, fixed in main by allowlisting published names)

## Known Facts & Context

Repo directives (project `AGENTS.md`): edit `payload/` then `python install.py`;
public repo — no user accounts / machine paths / private names; before commit all
three `audit_config.py` invocations must PASS (see AGENTS.md for exact commands);
design decisions append to `.agents/dotagents.md` (F#/D# numbering).

**Decisions already made (do not revisit):**
- **D-init**: `init` writes the *minimal neutral skeleton*, not the author payload.
  `install` writes the full `payload/` (today's behavior). Skeleton explains the
  `.agents` hierarchy, the per-agent `<CLAUDE|ANTIGRAVITY|…>.md → @AGENTS.md`
  pattern, and the `findings/` mechanism — imposes no flows.
- **D-source**: both `init` and `install` accept `--from <uri-or-path>` to pull the
  payload from an arbitrary `pathlib_next` `UriPath` (git checkout dir, `file:`,
  `http(s):`, `zip:`, `sftp:`, `s3:`). Default source for `init` is the bundled
  skeleton; for `install`, the bundled full payload.
- **D-vendor**: `build-pyz` vendors deps via `pip install --target <staging>` of
  pinned `duho`/`pathlib_next`, then `zipapp`. PyPI package itself declares them as
  normal `dependencies` (src-only wheel, matching author's other packages).
- **D-wrapper**: `install` writes `dotagents` + `dotagents.cmd` wrappers; location is
  flag-driven: `--bin-dir` (default `~/.local/bin`, allow `~/.agents/bin` or a
  project `.agents/bin`). If chosen dir is not on `PATH`, print a warning + the
  literal line to add it. Wrappers exec the pyz (or `python -m dotagents`).
- **D-init-merge**: `init` must **never clobber a user-customized `AGENTS.md`**.
  Unlike `install` (full-tree sync + timestamped backup), `init`'s `AGENTS.md`
  content is wrapped in sentinel markers and managed as a *block within* the file:
  - Skeleton `AGENTS.md` content is delimited by literal marker lines
    `<!-- dotagents:begin -->` … `<!-- dotagents:end -->`.
  - `init` on a **missing** `AGENTS.md`: write the file = the full managed block.
  - `init` on an **existing** `AGENTS.md` **without** the markers: **prepend** the
    managed block (block + blank line) to the top, leaving all existing content
    below untouched. *Why prepend*: scaffolding rules should be read first; the user's
    additions layer on top per the deeper-extends-broader convention.
  - `init` on an **existing** `AGENTS.md` **with** the markers: replace only the text
    *between* the markers with the current skeleton block; content outside the markers
    is never touched. This makes re-running `init` an idempotent block-refresh, and
    "our content is present" is a marker check, not a fragile substring/prose match.
  - Detection is by marker presence, never by matching prose (survives user reformat).
  - Same managed-block rule applies to the skeleton `CLAUDE.md` one-liner: if the file
    exists and already contains `@AGENTS.md` (any form), leave it; else prepend the
    marker block. Other skeleton files (`dotagents/log.md`, findings dirs, hierarchy
    doc): create-if-absent only, **never overwrite** an existing file.
  - `--force` opt-out flag: replace the whole `AGENTS.md`/`CLAUDE.md` (with the same
    timestamped backup as `install`) instead of the block merge, for users who want a
    clean reset. Default is the safe block-merge above.

**Dependencies (both are the author's own packages, sibling checkouts at build):**
- `duho` 0.1.1 — declarative CLI. API: subclass `duho.LoggingArgs`; class attrs =
  args with `"docstring"` + `("--flag",)` tuple; `_parsername_` names a subcommand;
  `_subcommands_ = [Sub1, Sub2]`; `_version_`; instance `__run__(self) -> int`;
  dispatch via `duho.main(RootArgs)`. Ref skeleton:
  `../duho/examples/dotagents.py` (working `install` subcommand stub — lift its shape).
  Positionals/Union/enum surface shown in `../duho/examples/buildutils.py`.
- `pathlib_next` — `from pathlib_next import LocalPath, MemPath`; `UriPath` for URI
  schemes (needs `uri` extra = `uritools` for any non-local scheme; `http`/`sftp`/
  `s3` extras add those schemes and degrade gracefully when absent).
  - `Path.copy(target, ...)` at `../pathlib_next/src/pathlib_next/path.py:601`,
    `.move()` :668, `.rm(...)` :523, `.walk(...)` :397.
  - `PathSyncer` at `../pathlib_next/src/pathlib_next/utils/sync.py:93`:
    `__init__(...)` :109 accepts an event `hook`; `sync(source, target, *,
    dry_run=False, ...)` :180 is one-way checksum-driven with `SyncEvent` enum
    (`Copy`, `Synced`, …) passed to the hook. Use its `dry_run` + hook to reproduce
    the current installer's "backup changed files, report copied/backed-up/unchanged"
    behavior instead of the hand-rolled `read_bytes()==read_bytes()` loop.

**Current installer behavior to preserve** (`install.py`, being replaced): copies
`PAYLOAD = [AGENTS.md, CLAUDE.md, MODELS.md, dotagents, flows, kb, references, tools]`
1:1 into dest (default `~/.agents`); a file overwritten with *different* content is
first backed up to `<dest>/install_backup/<timestamp>/`; unchanged files skipped;
`--with-examples` additively copies `payload/examples/` (never overwrites, reports
skips); `--dry-run`; ends by running `<dest>/tools/audit_config.py --root <dest>`.
Reports counts: `N installed, M backed up (path), K unchanged`.

**Python floor**: 3.9 (matches duho/pathlib_next `requires-python`). Skeleton
template check in audit needs 3.11+ (existing `--check-templates` constraint).

## Design questions

**Design Q1 — where does the bundled payload live inside the package?**
Decision: package data under `src/dotagents/skeleton/` (neutral starter) and the
build step copies the repo's `payload/` into the pyz separately at `build-pyz` time
(NOT committed into `src/`). Rationale: `payload/` is the author's product and stays
the single source of truth at repo root; duplicating it into `src/` would drift.
`install` from a *pip-installed* package (no repo) therefore requires `--from`
pointing at a payload source; `install` from the *pyz* uses the payload bundled at
build time. State this limitation in `install --help` and README.

**Design Q2 — pyz entry point.**
Decision: `zipapp` with `__main__.py` calling `dotagents.cli.main()`; `build-pyz`
stages `src/dotagents/`, the pip-vendored deps, and a copy of repo `payload/` into
one dir, then `python -m zipapp <stage> -o dist/dotagents.pyz -p "/usr/bin/env python3"`.

## Phases

### Phase 1 — Package skeleton + pyproject
Files: create `pyproject.toml` (root), `src/dotagents/__init__.py`,
`src/dotagents/__main__.py`, `src/dotagents/cli.py` (stub `main()` returning 0).
Guidance: hatchling backend, `[project.scripts] dotagents = "dotagents.cli:main"`,
`dependencies = ["duho>=0.1.1", "pathlib_next>=<pin>"]`, optional-extras mirroring
pathlib_next (`uri`, `http`, `sftp`, `s3`) so `dotagents[uri]` pulls the URI stack.
Copy classifier/urls shape from `../duho/pyproject.toml`. `requires-python = ">=3.9"`.
Pin `pathlib_next` to its current released version — check `pip index versions
pathlib_next` (or `../pathlib_next/pyproject.toml` `version`) and record the exact
pin in the config design log. *Why*: author packages ship src-only with real deps;
pyz vendoring is a separate concern (Phase 4).
Done when: `pip install -e .` succeeds and `python -m dotagents` exits 0.

### Phase 2 — Minimal neutral skeleton payload
Files: create `src/dotagents/skeleton/` containing: `AGENTS.md` (minimal — always-on
scaffolding: git/leakage/plans/findings rules stated generically, **empty** load-on-
demand list; the file's managed content wrapped in `<!-- dotagents:begin -->` /
`<!-- dotagents:end -->` marker lines per D-init-merge), `CLAUDE.md` (`@AGENTS.md`
one-liner, also marker-wrapped), `dotagents/log.md` (empty seed +
one-paragraph "how findings→config works"), `dotagents/findings/.gitkeep`,
`dotagents/findings/processed/.gitkeep`, and a `README.md` (or `HIERARCHY.md`)
explaining: `.agents/` location, per-agent `<AGENT>.md → @AGENTS.md` pattern, the
`findings/` capture-and-triage mechanism, and how to grow one's own flows from
findings (pointing at the repo's `examples/` for opt-in starters). *Why*: this is the
"understand the hierarchy, build your own" starter — must NOT contain the author's
flows/MODELS/REPO opinions (D-init).
Guidance: keep `AGENTS.md` lean; do not copy `payload/AGENTS.md` verbatim (it carries
the author's load-on-demand routing). Write fresh, generic wording.
Done when: skeleton tree exists and `grep -rl 'flows/PLAN\|flows/EXEC\|MODELS.md'
src/dotagents/skeleton/` returns nothing (no author-flow references).

### Phase 3 — CLI: init / install / audit subcommands
Files: `src/dotagents/cli.py`, `src/dotagents/_sync.py` (PathSyncer wrapper, used by
`install`), and `src/dotagents/_merge.py` (managed-block merge for `init`'s
`AGENTS.md`/`CLAUDE.md` per D-init-merge — marker constants, `merge_block(target,
block, *, force, dry_run) -> branch_name`, and an `@AGENTS.md`-present check for
`CLAUDE.md`).
Guidance: model root + subcommands on `../duho/examples/dotagents.py`. Classes:
- `Init(LoggingArgs)` `_parsername_="init"`: `--dest` (default `~/.agents`),
  `--from` (default = bundled skeleton dir, resolved via `importlib.resources` /
  package path), `--dry-run`, `--force`. Per-file behavior per **D-init-merge**, NOT a
  plain sync: `AGENTS.md`/`CLAUDE.md` go through the managed-block merge in
  `_merge.py` (create / prepend-block / refresh-between-markers / `@AGENTS.md`-present
  skip / `--force` full-replace-with-backup); every other skeleton file is
  create-if-absent (never overwrite). Report per file which branch was taken
  (`created` / `block-inserted` / `block-refreshed` / `skipped (present)` /
  `replaced (--force, backed up)`).
- `Install(LoggingArgs)` `_parsername_="install"`: `--dest`, `--from` (default =
  bundled/repo payload; required if package has no bundled payload — detect and error
  with the `--from` hint per Q1), `--with-examples`, `--bin-dir`, `--dry-run`.
- `Audit(LoggingArgs)` `_parsername_="audit"`: wraps `tools/audit_config.py` in the
  dest (or repo `payload/`); passes through `--root`/`--check-templates`/
  `--repo-hygiene`.
- `Dotagents(LoggingArgs)`: `_version_`, `_subcommands_=[Init, Install, Audit,
  BuildPyz]`; `main()` = `duho.main(Dotagents)`.
`_sync.py`: given a source `Path`/`UriPath` and dest `LocalPath`, run `PathSyncer`
with a hook that, on a would-overwrite of changed content, copies the old target into
`<dest>/install_backup/<timestamp>/` before the sync writes; count and report
installed/backed-up/unchanged exactly as the current installer's final line.
`--dry-run` → `PathSyncer.sync(..., dry_run=True)`. `--from` a URI string → construct
via `pathlib_next` `UriPath` (import lazily; if the needed extra is missing, error
with the literal `pip install "dotagents[uri]"` hint).
*Why*: reuse PathSyncer's checksum logic + hook rather than reimplement backup diff.
Done when: `init --dest <tmp> --dry-run` reports would-create branches + writes
nothing; real run populates `<tmp>`; re-run reports `block-refreshed`/`skipped
(present)` and leaves files byte-identical (idempotent). Merge proof: create `<tmp3>/
AGENTS.md` with a user line, run `init --dest <tmp3>` → file now begins with the
`<!-- dotagents:begin -->` block AND still contains the user line below; run again →
byte-identical (block refreshed in place, user line intact). `install --dest <tmp2>
--from payload` reproduces the old file set; `audit --root <tmp2>` runs the auditor.

### Phase 4 — build-pyz + wrapper install
Files: `src/dotagents/cli.py` (`BuildPyz` class), `src/dotagents/_wrappers.py`.
Guidance:
- `BuildPyz(LoggingArgs)` `_parsername_="build-pyz"`: `--out` (default
  `dist/dotagents.pyz`), `--python` (shebang, default `/usr/bin/env python3`).
  Steps (literal): make a temp stage dir; `pip install --target <stage> duho==0.1.1
  pathlib_next==<pin>` (no extras — pyz ships stdlib-only cores; URI/http/sftp/s3
  schemes require the user to `pip install` those into their env, document this);
  copy `src/dotagents/` into `<stage>/dotagents`; copy repo `payload/` into
  `<stage>/dotagents/_payload/` (so a downloaded pyz can `install` the full author
  payload offline); write `<stage>/__main__.py` → `from dotagents.cli import main;
  raise SystemExit(main())`; run `python -m zipapp <stage> -o <out> -p "<python>"`.
  Prune `__pycache__`, `*.dist-info`, tests from the stage before zipping.
- `_wrappers.py`: `write_wrappers(bin_dir, pyz_path)` writes `dotagents` (POSIX sh:
  `exec python3 "<pyz>" "$@"`) and `dotagents.cmd` (`@python "%~dp0dotagents.pyz" %*`
  or absolute pyz path); `chmod +x` the sh wrapper (guard on non-POSIX). Called by
  `Install` when `--bin-dir` resolution succeeds; PATH check + literal export hint if
  the dir isn't on `PATH`.
*Why*: pyz must run on a bare machine → deps physically vendored (D-vendor); wrappers
give a real `dotagents` command (D-wrapper).
Done when: `python -m dotagents build-pyz --out <tmp>.pyz` produces a file; `python
<tmp>.pyz --version` prints the version on a clean venv with only stdlib; `python
<tmp>.pyz install --dest <t> --bin-dir <t>/bin` installs payload + writes both
wrappers and `<t>/bin/dotagents --version` works.

### Phase 5 — Replace root install.py, docs, CI, hygiene
Files: `install.py` (replace body), `README.md`, `CHANGELOG.md`,
`.agents/dotagents.md`, `.github/workflows/*` (if a build/test workflow
exists — check first), `.gitignore` (ignore `dist/`, `*.pyz`, build stage, `src/*.egg-info`).
Guidance:
- `install.py` becomes a thin shim: `import sys; from dotagents.cli import main;
  sys.exit(main())` prefixed with a `sys.path.insert(0, "src")` fallback so
  `python install.py install` works from a raw checkout without `pip install`. Keep
  the filename so existing muscle-memory/docs entry point survives.
- README: document `dotagents init` (minimal), `dotagents install` (full payload),
  the downloadable `dotagents.pyz` quick-start (`curl`/download → `python
  dotagents.pyz install`), and the `--from` URI capability. Note pyz install needs no
  pip; PyPI install of URI schemes needs extras.
- CHANGELOG: new entry (`feat:`—CLI package + pyz).
- Append D-init/D-source/D-vendor/D-wrapper + the pathlib_next pin (F#/D#) to
  `.agents/dotagents.md`.
*Why*: single documented entry point stays `install.py`; publishing pyz is a release
artifact, not committed.
Done when: `python install.py init --dest <tmp> --dry-run` works from a fresh clone
without prior `pip install`; README shows the pyz path; all three audit commands PASS.

## Verification
Run from repo root on a standard checkout (sibling `../duho`, `../pathlib_next`
present for build). Setup first:
```
python -m venv .venv-test && .venv-test/Scripts/python -m pip install -e . -e ../duho -e ../pathlib_next
```
Then:
```
.venv-test/Scripts/python -m dotagents --version
.venv-test/Scripts/python -m dotagents init --dest /tmp/da-init --dry-run
.venv-test/Scripts/python -m dotagents init --dest /tmp/da-init
.venv-test/Scripts/python -m dotagents init --dest /tmp/da-init            # 2nd run: block-refreshed/skipped, idempotent
printf 'my custom rule\n' > /tmp/da-merge/AGENTS.md                        # pre-existing user file
.venv-test/Scripts/python -m dotagents init --dest /tmp/da-merge           # prepends managed block, keeps user line
.venv-test/Scripts/python -m dotagents init --dest /tmp/da-merge           # 2nd run byte-identical
.venv-test/Scripts/python -m dotagents install --dest /tmp/da-full --from payload
.venv-test/Scripts/python -m dotagents build-pyz --out /tmp/dotagents.pyz
python /tmp/dotagents.pyz --version                                        # clean stdlib run
python /tmp/dotagents.pyz install --dest /tmp/da-pyz --bin-dir /tmp/da-pyz/bin
python payload/tools/audit_config.py --root payload
python payload/tools/audit_config.py --check-templates --root payload
python payload/tools/audit_config.py --repo-hygiene .
```
Expected: version prints on every path incl. bare pyz; first `init` reports would-
create branches + writes nothing on dry-run, real run populates, re-run is idempotent;
the `/tmp/da-merge/AGENTS.md` after `init` starts with `<!-- dotagents:begin -->` and
still contains `my custom rule`, and the second `init` leaves it byte-identical; `install --from payload` reproduces the legacy file set
(AGENTS/CLAUDE/MODELS/dotagents/flows/kb/references/tools); pyz install writes
`dotagents` + `dotagents.cmd` and the wrapper `--version` works; all three audits PASS.
Skeleton contains zero author-flow references (Phase 2 grep empty).

## Reporting
Keep `## Progress` boxes live (`[/]` on start, `[x]` on done). Architecture notes or
gotchas found during execution (e.g. duho arg quirks, PathSyncer hook timing,
zipapp/pip-target pitfalls) go into the repo root `AGENTS.md` or a subtree `AGENTS.md`,
not this plan. Config decisions append to `.agents/dotagents.md`.
```
```
