# Decompose payload/ into composable named overlays

Status: done
Executor: self (main thread — holds full context from the CLI + split work this
session; a cold subagent would re-derive payload layout, CLI structure, duho/
pathlib_next APIs, and the Store-Python/venv gotchas at higher total cost. Cheapest-
correct = finish warm, not delegate). Sonnet-tier judgment, already loaded.

Replace the monolithic `payload/` (one "full config" that `install` copies 1:1) with a
**base overlay** + **composable named overlays** the user opts into. `init` lays down
the base; `install`/`add` applies the base plus chosen overlays, each contributing its
own routing lines to `AGENTS.md`. Do this on the local `feat/dotagents-cli-and-log-split`
branch BEFORE any push (user directive: design + rename, then push once).

## Progress
- [x] Phase 1 — layout + manifest format confirmed (duho has tomllib/tomli `_load_config`)
- [x] Phase 2 — split done: overlays/{base,flows,recovery,tools,references,python,node,
      rust,agents}/ each with overlay.toml; required tools → top-level tools/; payload/
      + examples/ + src/dotagents/_overlay/ removed; no empty scaffold dirs
- [x] Phase 3 — CLI: overlay-AGNOSTIC install (base + opt-in --overlays <path>, copy
      only; heavy resolve/routing-merge machinery deferred to a future overlays
      subcommand per user). init/install verified on 3.14 + 3.9; idempotent; clean error.
- [x] Phase 4 — Update audit_config.py manifest, CI, README, AGENTS.md, decisions
- [x] Phase 5 — Verify (audits, final CLI pass)

## Known Facts & Context

Repo directives (`AGENTS.md`): edit source, run `python install.py`; public repo —
sanitize (no user accounts/machine paths/private names); before commit all three
`audit_config.py` invocations must PASS; decisions → `.agents/dotagents/` (one file per
`decisions/D<nn>.md`, index `DECISIONS.md`). Continue D-numbering (last is **D34**).

**Environment** (from `~/.agents/AGENTS.local.md` + D34): use real Python via Python
Manager, NOT Store Python (its pip silently hangs). Verified good: `py -3.14`
(`AppData\Local\Python\pythoncore-3.14-64\`), `py -3.9` (floor). Venvs:
`.venv/<ver>-<os>-<arch>` (e.g. `.venv/3.14-nt-amd64`); this session's venvs exist.
`--check-templates` needs 3.11+ (3.14 works, has tomllib). `pathlib_next` 0.8.0 needs
`typing_extensions` on the 3.9 venv.

**Current layout** (all on the branch, committed):
- `payload/` full config: `AGENTS.md`, `CLAUDE.md`, `MODELS.md`, `dotagents/`,
  `flows/{PLAN,EXEC,REVIEW,REPO}.md`, `kb/RECOVERY.md`, `references/*` (README,
  CHANGELOG, LICENSE, .gitignore, docs-index, master_refactoring_plan),
  `tools/{audit_config,leak_check,summarize_run,compare_bench}.py`.
- `payload/examples/`: `antigravity.md`, `kb/{PYTHON,NODE,RUST}.md`,
  `references/{Cargo.toml,mkdocs.yml,package.json,pyproject.toml}`,
  `references/workflows/{python,node,rust}/{test,release}.*`.
- `src/dotagents/_overlay/` — the neutral base overlay `init` writes (minimal
  `AGENTS.md` with EMPTY load-on-demand list, `CLAUDE.md`, `README.md`,
  `dotagents/DECISIONS.md` + dirs). `src/dotagents/cli.py` has
  `PAYLOAD_ENTRIES`, `OVERLAY_ROOT`/`OVERLAY_PLAIN_FILES`, `BUNDLED_PAYLOAD =
  _package_data_dir("_payload")`. `install` copies `PAYLOAD_ENTRIES` from a payload
  source; `build-pyz` bundles repo `payload/` as `_payload/`.
- `_merge.py` merges marker-delimited (`<!-- dotagents:begin -->`/`end`) blocks into
  `AGENTS.md`/`CLAUDE.md` — the mechanism overlays will use to add routing lines.

**Key architectural insight**: an overlay is NOT just a file drop. The base
`AGENTS.md` carries a "## Load on demand" routing list; each overlay that adds a
`flows/`/`kb/` file must also add its routing line to `AGENTS.md`. So every overlay
ships (a) its files and (b) a routing-lines fragment merged into the base `AGENTS.md`
load-on-demand section. `_merge.py`'s managed block is per-file; extend it to support a
named sub-region for routing so multiple overlays append without clobbering each other,
OR give each overlay its own marker pair. **Design Q3 resolves this.**

## Design questions

**Design Q1 — overlay boundaries (user chose "split by concern now").**
Decision: these named overlays under a new top-level `overlays/` dir (NOT under
`examples/` — see Q2):
- `overlays/base/` — the neutral minimum. This SUPERSEDES `src/dotagents/_overlay/` as
  the single source of the base (Q4). Contents: minimal `AGENTS.md` (empty routing),
  `CLAUDE.md`, `README.md`, `dotagents/DECISIONS.md`. **No empty `dotagents/decisions/`
  dir or `.gitkeep`** (user cleanup): `DECISIONS.md` documents the index+per-file
  convention; the `decisions/` dir is created lazily when the first decision is
  written. Likewise no empty `findings/` scaffold — `DECISIONS.md`/`AGENTS.md` name
  the path; the dir is created on first capture. (Update `cli.py`
  `OVERLAY_PLAIN_FILES` to drop the `.gitkeep` entries accordingly.)
- `overlays/flows/` — `flows/{PLAN,EXEC,REVIEW,REPO}.md` + `MODELS.md` + their
  `AGENTS.md` routing lines. (Planning/execution/review/repo/model workflow.)
- `overlays/recovery/` — `kb/RECOVERY.md` + routing line.
- `overlays/tools/` — `tools/{summarize_run,compare_bench}.py` + the always-on
  token-discipline wording fragment. (`audit_config.py`/`leak_check.py` are
  dotagents-*required* tooling, not a user overlay — Q5.)
- `overlays/python/`, `overlays/node/`, `overlays/rust/` — each: `kb/<LANG>.md` +
  `references/<lang manifests/workflows>` + routing line. (From today's `examples/`.)
- `overlays/agents/` — `antigravity.md` (+ any named-agent directives) + routing.
- `overlays/references/` — language-neutral templates (README, CHANGELOG, LICENSE,
  .gitignore, docs-index, master_refactoring_plan). Referenced by `flows/REPO.md`, so
  `flows` overlay depends on it (Q3 dependency handling).
Each overlay dir contains an `overlay.toml` manifest (Q3) + its files mirroring the
`~/.agents/` layout they install into.

**Design Q2 — where do overlays live: `overlays/` vs `examples/<name>/`?**
Decision: top-level **`overlays/`**, not `examples/`. Rationale: "examples" implies
non-installable samples; these are the real installable units. Keep the word
"examples" only for genuinely illustrative, non-managed content if any remains (none
does after this split — retire `payload/examples/`). *Why deviate from the user's
"examples/<name>" phrasing*: the user's intent was composable named overlays; the
observable requirement is a clear installable-unit dir, and `overlays/` names that
directly. If the user prefers `examples/`, it's a pure `git mv` of the top dir — note
in Reporting.

**Design Q3 — overlay manifest + dependency + routing-merge format.**
Decision: each `overlays/<name>/overlay.toml` declares:
```
name = "python"
description = "Python repo conventions: kb, manifests, CI workflows"
requires = ["references"]        # other overlays this one needs
routing = ['''- Language-specific work → ~/.agents/kb/python.md ...''']  # lines merged into base AGENTS.md load-on-demand
```
Files under the overlay dir install to the same relative path in the dest. The CLI
resolves `requires` transitively, applies base first, then overlays in dependency
order, concatenating each overlay's `routing` fragment into ONE managed block in the
dest `AGENTS.md` (single `<!-- dotagents:routing:begin -->`/`end` region rebuilt from
the selected set each run — idempotent, order-stable). *Why one rebuilt region, not
per-overlay markers*: removing an overlay later must drop its routing lines; a single
region regenerated from the active set makes add/remove clean. **This needs `duho`'s
TOML support** — duho has a `config` extra (`tomli; python_version < '3.11'`); confirm
it's declared or add `tomli` to the floor. Verify against the real duho at
`../duho`.

**Design Q4 — base stays as `src/dotagents/_overlay/` (REVISED per user).**
Decision: base does NOT move into `overlays/`. It stays `src/dotagents/_overlay/`
(package data, already bundled — `init` works with no repo). `overlays/` holds ONLY the
opt-in overlays. Rationale (user): base and opt-in overlays play different roles —
base is the always-applied scaffold `init` lays down; overlays are opt-in. Forcing base
to look "uniform" under `overlays/` blurs that and adds bundling wiring for no gain.
`init` = apply base (`_overlay`) only; `install` = base + all overlays (old full-payload
behavior) or `--overlay <name>` subset; `dotagents add <name>` applies more later. The
empty `dotagents/{decisions,findings}` scaffold + `.gitkeep`s are dropped from base
(created lazily). `build-pyz` bundles `overlays/` (as `_overlays`) alongside `_overlay`.

**Design Q5 — required tooling vs overlay tooling** (already decided this session, in
the draft `tools_as_dotagents_subcommands.md`): `audit_config.py` + `leak_check.py` are
dotagents-required (they validate/scan the config itself) → they stay as CLI-adjacent
tooling, NOT in a user overlay; fold into `dotagents audit`/`leak-check` per that draft
(out of THIS plan's scope — this plan only relocates `summarize_run`/`compare_bench`
into `overlays/tools/`). Keep `audit_config.py` importable at its current path so the
three commit-gate commands still work; note if its path must change.

## Phases

### Phase 1 — layout + manifest
Finalize `overlays/` structure + write one `overlay.toml` per overlay (Q1/Q3). No file
moves yet — just the target tree + manifests drafted in the plan's Known Facts if
refined. Confirm duho TOML support against `../duho/pyproject.toml` +
`../duho/src/duho/`. Done when: manifest format validated against real duho; overlay
list final.

### Phase 2 — physical split
`git mv` payload files into `overlays/<name>/` per Q1. **Retire `payload/examples/`
entirely** (user cleanup): its contents become real overlays — `kb/{PYTHON,NODE,RUST}.md`
+ their `references/` manifests/workflows → `overlays/{python,node,rust}/`;
`antigravity.md` → `overlays/agents/`; the language-neutral `references/*` →
`overlays/references/`. No `examples/` dir remains anywhere. Create each `overlay.toml`;
delete `src/dotagents/_overlay/` (Q4) and the now-empty `payload/`. **Drop empty
scaffold dirs**: base overlay ships no `dotagents/decisions/`, `dotagents/findings/`, or
`.gitkeep` files — these dirs are created lazily (by the CLI on install where a file
lands in them, and by agents on first findings-capture per the "create if absent" rule).
Base `AGENTS.md` keeps empty routing; each overlay's routing lives in its manifest.
Done when: `payload/` and all `examples/` gone; `overlays/` holds base + named overlays
each with a manifest; no `.gitkeep`-only dirs; `git status` shows only moves/adds.

### Phase 3 — CLI overlay-aware install
`cli.py`: `init` applies `overlays/base` only. `install` applies base + all overlays
(default) or `--overlay <name>` (repeatable) subset; resolves `requires`; rebuilds the
single AGENTS.md routing region from the active set via `_merge.py` (extend it for the
`routing` region). New `Add(LoggingArgs)` `dotagents add <name>`. `--from` still selects
the source root (now containing `overlays/`). `build-pyz` bundles `overlays/`. Read
`overlay.toml` via duho's TOML (or `tomllib`/`tomli`). Done when: `dotagents init`
writes base; `dotagents install` reproduces today's full config (all flows/kb/tools/
references present) with a correct merged routing list; `dotagents install --overlay
python` writes base+references+python only; re-run idempotent.

### Phase 4 — manifest/audit/docs/decisions
Update `audit_config.py` SCAN/REF manifests to the new paths (base `AGENTS.md`,
`overlays/flows/*`, etc.); keep all three audits PASS. Update CI `test.yml`, `README.md`
(explain base + overlays + `add`), root `AGENTS.md` "edit here" paths, and add decisions
**D35** (overlay decomposition, supersedes D24's "payload isolated under payload/"),
**D36** (overlay manifest/routing-merge format). Update `pyproject.toml` package-data if
needed. Done when: audits PASS; README documents the model; D24 marked superseded by
D35 in its file.

### Phase 5 — verify
Create/refresh venvs, install the package, exercise the CLI end-to-end on latest+floor.
Done when: all Verification below passes.

## Verification
```
cd <repo-root>
# env (real python, not Store)
py -3.14 -m venv .venv/3.14-nt-amd64 2>/dev/null; PY=.venv/3.14-nt-amd64/Scripts/python.exe
$PY -m pip install -q --timeout 20 --retries 1 duho pathlib_next -e .
$PY -m dotagents --version
$PY -m dotagents init --dest /tmp/ov-base && test -f /tmp/ov-base/AGENTS.md \
  && ! grep -q 'Load on demand.*→' /tmp/ov-base/AGENTS.md   # base has empty routing
$PY -m dotagents install --dest /tmp/ov-full --from .        # base + all overlays
test -f /tmp/ov-full/flows/PLAN.md && test -f /tmp/ov-full/MODELS.md \
  && test -f /tmp/ov-full/kb/RECOVERY.md && grep -q 'Load on demand' /tmp/ov-full/AGENTS.md
$PY -m dotagents install --dest /tmp/ov-py --from . --overlay python  # base+references+python
test -f /tmp/ov-py/kb/python.md && test ! -f /tmp/ov-py/flows/PLAN.md
$PY -m dotagents add --dest /tmp/ov-py --from . rust && test -f /tmp/ov-py/kb/rust.md
$PY -m dotagents install --dest /tmp/ov-full --from .         # 2nd run idempotent (no changes)
py -3.14 payload/tools/audit_config.py --root overlays/flows   # (path per Phase 4)
py -3.14 <audit tool path> --check-templates --root <refs overlay>
py -3.14 <audit tool path> --repo-hygiene .
# floor
py -3.9 -m venv .venv/3.9-nt-amd64 2>/dev/null; PY9=.venv/3.9-nt-amd64/Scripts/python.exe
$PY9 -m pip install -q duho pathlib_next typing_extensions -e . && $PY9 -m dotagents --version
```
Expected: version prints on 3.14 + 3.9; base install has empty routing; full install
reproduces every flows/kb/tools/references file with a populated merged routing list;
`--overlay python` is a correct subset; `add rust` extends it; re-run idempotent; all
three audits PASS. The audit tool path (Phase 4/Q5) is stated in the final plan before
these commands run.

## Reporting
Live-update Progress. Record D35/D36; mark D24 superseded. If the user prefers
`examples/<name>/` over `overlays/`, note the single `git mv`. Nothing pushed — the
whole point is to finalize naming before the first push. After this lands, the 6
existing branch commits + these become one coherent public history.
```
```
