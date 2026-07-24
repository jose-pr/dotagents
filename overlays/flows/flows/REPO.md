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

## CI — three workflow files, one per concern (test, release, docs)
Why three: a release must never be the first time a config is exercised, testing must
never require cutting a release, and the docs site must be redeployable without a
release (so it stays current between them).
- **Test workflow**: `workflow_dispatch` (with a `ref` input) + throwaway `ci-*` tag
  trigger; not necessarily every push/PR. Agents may push `ci-*` tags without asking —
  use unique names (`ci-<topic>-<timestamp>`), poll the run to completion, report the
  result, then delete the tag local + remote. Never leave `ci-*` tags behind.
- **Release workflow** (`v*` tag): test gate → build → publish, in dependency order,
  plus a **docs-gate** job that runs the docs build strictly but does **not** deploy
  (a broken docs site must block the release without the release owning deployment).
  Publish steps are re-run-safe (e.g. `skip-existing` on the registry upload).
  Prefer the registry's OIDC / trusted publishing over stored long-lived token secrets.
- **Docs workflow**: builds + deploys the docs site on its own trigger (push to the
  default branch touching docs sources, plus `workflow_dispatch`), so the published
  site tracks the branch and can be redeployed without a release. The docs site is a
  required deliverable even if nothing else needs it. Ecosystems that host API docs
  externally (e.g. Rust/docs.rs) only need this workflow for a narrative guide.
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
- `README.md`, `.gitignore`: from `references/`. `README.md` is the package
  long-description (`readme=` in the manifest) and MUST ship in the built package
  (sdist + wheel). `.gitignore` excludes `.agents` (slashless — `dotagents link` makes
  it a symlink, which a directory-only `.agents/` won't match), `CLAUDE*`, `.claude`,
  plus the language's build output.
- `src/**/AGENTS.md` — one committed, package-shipped "header" per module dir: that
  module's public API header-file-style (exports with signatures/args/defaults,
  return-or-contract, env vars, gotchas) so a consuming agent skips the source. Current
  with the API, same commit. **No repo-root `AGENTS.md`** — private working notes live
  in `.agents/AGENTS.md`, human-facing overview in `README.md`.
- `CHANGELOG.md`: Keep a Changelog format (`references/CHANGELOG.md`) — `[Unreleased]`
  always at top, one `## [x.y.z] - <date>` heading per release. Before release, run
  `py -3.12 ~/.agents/tools/leak_check.py <repo>` — it scans tracked files for
  private-plan leaks AND commit messages for agent-session trailers/URLs
  (`Claude-Session:`, `claude.ai/code/session`). That trailer is auto-added by the
  harness and must be stripped before pushing public — the link exposes a session id.
  If one already landed, rewrite it out
  (`git filter-branch --msg-filter "sed '/^Claude-Session:/d'"`); pre-rewrite SHAs stay
  reachable on the host until it GCs.
- `RELEASENOTES.md` (repos with a perf story): the detailed companion to a terse
  `CHANGELOG.md` — the durable audit trail for perf claims and release decisions. Per
  release: a **previous→current benchmark table** (median) from `.agents/bench/`;
  **migration nuance** too detailed for Keep a Changelog; **benchmark caveats** (runner
  noise, OS, language version, local-vs-CI); **validation evidence** (tests/build/docs/
  leak-check, CI run IDs); **publication state** (prepared / main pushed / tag awaiting
  per-release consent). `[Unreleased]` records the next perf target so a regression is
  caught before tagging. Same leak rules as public notes.
- `LICENSE`: MIT by default (`references/LICENSE`; fill `<year>`/`<copyright_holder>`).
- Template files may contain hidden `<!-- EXECUTOR: ... -->` comments — strip them
  before writing real repo files.

## Versioning
SemVer for git tags and `CHANGELOG.md`. The package manifest's own version syntax may
legitimately differ (the language doc says how, e.g. PEP 440) — never "fix" one to
match the other.

Per-language CI templates (opt-in examples): `~/.agents/examples/references/workflows/<lang>/`.
