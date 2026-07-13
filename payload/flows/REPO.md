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
- `benchmarks/`: a dedicated perf suite, runnable by a documented command.
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
  `.gitignore` always excludes agent artifacts (`AGENTS.md`, `.agents/`, `CLAUDE*`,
  `.claude`) plus the language's build/dependency output.
- `CHANGELOG.md`: Keep a Changelog format (`references/CHANGELOG.md`) — `[Unreleased]`
  always at top, one `## [x.y.z] - <date>` heading per release. Before release, run
  `py -3.12 ~/.agents/tools/leak_check.py <repo>` — it scans tracked files for
  private-plan leaks (`.agents/` refs, plan filenames, agent `Phase N` phrasing).
- `LICENSE`: MIT by default (`references/LICENSE`; fill `<year>`/`<copyright_holder>`).
- Template files may contain hidden `<!-- EXECUTOR: ... -->` comments — strip them
  before writing real repo files.

## Versioning
SemVer for git tags and `CHANGELOG.md`. The package manifest's own version syntax may
legitimately differ (the language doc says how, e.g. PEP 440) — never "fix" one to
match the other.

Per-language CI templates (opt-in examples): `~/.agents/examples/references/workflows/<lang>/`.
