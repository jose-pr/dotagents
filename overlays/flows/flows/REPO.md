# Flow: Repository Standard (language-agnostic)

Applies when creating a repo or bringing one up to standard — a gap against this list
gets flagged or fixed, never silently skipped. If `~/.agents/kb/<LANG>.md` exists it
supplies the concrete toolchain and may override specifics (copy it in from
`~/.agents/examples/kb/<LANG>.md` first if missing and the user wants one); neither
file restates the other.

## Layout
- Source in its own directory (language doc names it, e.g. `src/`), separate from
  tests/docs/examples/benchmarks.
- `tests/`: a real suite, runnable by a single documented command.
- `benchmarks/`: a dedicated perf suite, runnable by a documented command. It emits
  one structured JSON result per (version, interpreter) into `.agents/bench/<name>.json`
  (a `--save` flag on the runner); each metric reports min/median/max ms-per-call over
  N samples (compare on median — a single `timeit` average hides real run-to-run noise).
  Keep a `.agents/bench/README.md` documenting the schema + reproduce command, and a
  baseline entry for the pre-optimization state so before/after is always recoverable.
  Run benchmarks on demand / manually, not as noisy per-push CI.
- `examples/`: runnable scripts/configs, not scratch files.
- `docs/` + a docs site: hand-written landing page (never a verbatim `README.md`
  embed — its relative links break once served from a docs-site root; see
  `~/.agents/references/docs-index.md`) plus auto-generated API reference where the
  ecosystem supports it (language doc names the generator, and whether a deploy step is
  even needed).

## CI — two workflow files covering three concerns (test, release, docs)
Why two: a release must never be the first time a config is exercised, and testing must
never require cutting a release.
- **Test workflow**: `workflow_dispatch` (with a `ref` input) + throwaway `ci-*` tag
  trigger; not necessarily every push/PR. Agents may push `ci-*` tags without asking —
  use unique names (`ci-<topic>-<timestamp>`), poll the run to completion, report the
  result, then delete the tag local + remote. Never leave `ci-*` tags behind.
- **Release workflow** (`v*` tag): test gate → build → publish, in dependency order,
  plus a docs build+deploy job gated on the same build (the docs site is a required
  deliverable even if nothing else needs it). Prefer the registry's OIDC / trusted
  publishing over stored long-lived token secrets.
- **Changelog-derived release notes**: repos without a PR flow get thin auto-generated
  "What's Changed" notes on every release — scrape the pushed tag's `CHANGELOG.md`
  section into the release body instead (keep the platform's auto compare-link
  addendum). GitHub's `ubuntu-latest` bundles Python 3 regardless of project language,
  so every language reuses the identical `shell: python` scraper step — never
  reimplement it per language. Public notes never mention private plan names,
  `.agents/plans`, reviewer ids, or agent `Phase N` labels; describe behavior/rationale.
- **Tagging discipline**: the `v*` push triggers everything and publish is normally
  irreversible (registries never reuse a version string). Finish every other step first
  so tagging is the only thing left, then ask the user — per release; a yes once is not
  standing consent. (`ci-*` is always safe — see core `AGENTS.md`.)

## Meta files (skeletons in `~/.agents/references/`; language doc adds specifics on top)
- `README.md`, `.gitignore`: from `references/README.md` / `references/.gitignore`;
  `README.md` is the package long-description (`readme=` in the manifest) and MUST ship
  inside the built package (sdist + wheel). `.gitignore` always excludes private agent
  artifacts (`.agents`, `CLAUDE*`, `.claude`) plus the language's build/dependency
  output — but NOT root `AGENTS.md` (a committed public doc, below). Note `.agents`
  has no trailing slash: `dotagents link` makes it a symlink, and a directory-only
  `.agents/` would not ignore a symlink.
- `AGENTS.md` — committed public agent docs, never gitignored (see core `AGENTS.md`):
  - **repo root `AGENTS.md`**: a dev-facing project overview — what it is, code layout,
    entry points, how to build/develop/use at a high level; links to the module headers.
  - **`src/**/AGENTS.md`**: one "header" file per source module/package dir, colocated
    with the code — that module's public API header-file-style (exports with
    signatures/accepted args/defaults/required, return-or-contract, env vars, gotchas)
    so a consuming agent uses it without a source dive; keep current with the public API
    (same commit). Ship these plus `README.md` inside the built package. Distinct from
    the private `.agents/AGENTS.md` working notes.
- `CHANGELOG.md`: Keep a Changelog format (`references/CHANGELOG.md`) — `[Unreleased]`
  always at top, one `## [x.y.z] - <date>` heading per release. Before release, run
  `py -3.12 ~/.agents/tools/leak_check.py <repo>` — it scans tracked files for
  private-plan leaks (`.agents/` refs, plan filenames, agent `Phase N` phrasing) AND
  commit messages for agent-session trailers/URLs (`Claude-Session:`,
  `claude.ai/code/session`). The session trailer is auto-added by the agent harness and
  must be stripped before pushing to a public repo — the link exposes a session id (the
  content is account-gated, but the id shouldn't be in public history). If one already
  landed, rewrite it out (`git filter-branch --msg-filter "sed '/^Claude-Session:/d'"`)
  and note that the pre-rewrite SHAs stay reachable on the host until it GCs.
- `RELEASENOTES.md` (repos with a perf story or non-trivial release narrative): the
  detailed companion to `CHANGELOG.md` — `CHANGELOG.md` stays terse and public; the
  durable audit trail for performance claims and release decisions lives here. Per
  release it carries: a **previous→current benchmark table** (median, plus
  unsupported-before cases) sourced from `.agents/bench/`; **behavior/migration nuance**
  too detailed for Keep a Changelog; **benchmark caveats** (shared-runner noise, OS,
  Python version, local-vs-CI); **validation evidence** (tests/build/docs/leak-check
  results, CI run IDs when available); and **publication state** (prepared / main pushed
  / tag not pushed until per-release user consent). `[Unreleased]` records the
  next-release perf target so a regression is caught before tagging. Same leak rules as
  public release notes: no plan names, `.agents` refs, or `Phase N` phrasing.
- `LICENSE`: MIT by default (`references/LICENSE`; fill `<year>`/`<copyright_holder>`).
- Template files may contain hidden `<!-- EXECUTOR: ... -->` comments — strip them
  before writing real repo files.

## Versioning
SemVer for git tags and `CHANGELOG.md`. The package manifest's own version syntax may
legitimately differ (the language doc says how, e.g. PEP 440) — never "fix" one to
match the other.

Per-language CI templates (opt-in examples): `~/.agents/examples/references/workflows/<lang>/`.
