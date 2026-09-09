# Flow: Repository Standard (language-agnostic)

Applies when creating a repo or bringing one up to standard — a gap against this list
gets flagged or fixed, never silently skipped. If `$<LANG>_OVERLAY_ROOT/kb/<LANG>.md`
exists it supplies the concrete toolchain and may override specifics (copy it in from
the `<lang>` overlay's `kb/<LANG>.md` first if missing and the user wants one); neither
file restates the other.

## Layout
- Source in its own directory (language doc names it, e.g. `src/`), separate from
  tests/docs/examples/benchmarks.
- `tests/`: a real suite, runnable by a single documented command.
- `benchmarks/`: a dedicated perf suite, runnable by a documented command. It emits
  one structured JSON result per (version, interpreter) into `benchmarks/results/<name>.json`
  (a `--save` flag on the runner); each metric reports min/median/max ms-per-call over
  N samples (compare on median — a single `timeit` average hides real run-to-run noise).
  Results are **tracked and committed** — that is what makes before/after recoverable
  (never `.agents/bench/`: `.agents/` is gitignored, so nothing there survives in
  history; precedent: one project). Keep a `benchmarks/README.md` documenting the
  schema + reproduce command, and a baseline entry for the pre-optimization state.
  Run benchmarks on demand / manually, not as noisy per-push CI.
- `examples/`: runnable scripts/configs, not scratch files.
- `docs/` + a docs site: hand-written landing page (never a verbatim `README.md`
  embed — its relative links break once served from a docs-site root; see
  `$REFERENCES_OVERLAY_ROOT/references/docs-index.md`) plus auto-generated API
  reference where the ecosystem supports it (language doc names the generator, and
  whether a deploy step is even needed).

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
- **Docs workflow** owns **all** Pages deploys, on three triggers: `release:
  published` (a `v*` release ships its matching docs — the release workflow only gates
  on a strict docs build, then its published release fires this deploy), push to the
  default branch touching docs sources (main = latest, between releases), and
  `workflow_dispatch` (manual redeploy of any ref). It self-enables Pages every run. The
  docs site is a required deliverable even if nothing else needs it. Ecosystems that
  host API docs externally (e.g. Rust/docs.rs) only need this workflow for a narrative guide.
- **Hosted docs site (GitHub Pages) — settings that must live in the workflow, not in
  repo settings a human clicked once.** A docs job that assumes pre-configured repo
  state fails on a fresh clone/fork and, because the docs job runs inside the release
  workflow, turns an otherwise-successful release red.
  - **Self-enable Pages**: `actions/configure-pages` with `enablement: true` plus
    `permissions: pages: write` on that job. Without it the step dies on a repo where
    Pages was never turned on — `Get Pages site failed ... Not Found` — and the
    404 names the REST endpoint, not the missing setting, so it reads like a broken
    action. Pages must be set to **build from GitHub Actions** (not "deploy from a
    branch"); `enablement: true` sets that too, which is why it beats manual setup.
  - **Every workflow that builds the docs needs this identically.** A standalone
    `docs.yml` that self-enables and a release workflow that does not is the exact
    shape that hides the bug: the docs site publishes fine all along, and only the
    release run fails.
  - **Deploy job**: `permissions: pages: write` + `id-token: write` and
    `environment: name: github-pages`. YAML schema linters flag `github-pages` as an
    invalid environment value — it is a false positive (GitHub reserves that name);
    do not "fix" it.
  - **Never gate publish on the docs job.** Keep `docs-build`/`docs-deploy` off the
    dependency chain of build → release → publish, so a docs-only problem can never
    block a package that already passed its tests.
  - **Two repo settings the workflow cannot set for itself** (both produce errors that
    name an API endpoint rather than the setting, so check them first):
    - *Default workflow permissions must be read **and** write* (repo → Settings →
      Actions → General, or
      `gh api -X PUT repos/<org>/<repo>/actions/permissions/workflow -f default_workflow_permissions=write`).
      While it is read-only, a job's `pages: write` cannot be granted and
      `configure-pages` fails with `Resource not accessible by integration` even with
      `enablement: true` — the self-enable is silently powerless. If it still fails
      after the change, enable Pages once with your own credentials:
      `gh api -X POST repos/<org>/<repo>/pages -f build_type=workflow`.
    - *The `github-pages` environment's deployment branch policy must allow the tag*.
      It defaults to the default branch only, so a release deploying docs from a `v*`
      tag is rejected with `Tag "vX.Y.Z" is not allowed to deploy to github-pages due
      to environment protection rules` — after a successful build, so it looks like a
      deploy bug rather than a policy. Add a tag policy:
      `gh api -X POST repos/<org>/<repo>/environments/github-pages/deployment-branch-policies -f name='v*' -f type=tag`.
- **Release objects must be pinned to the tagged commit**: pass the release action an
  explicit `target_commitish: ${{ github.sha }}`. Left unset it defaults to the
  repository's default branch, so a release object created before a branch rename
  (`master` → `main`) keeps pointing at a branch that no longer exists and every later
  release fails at note generation with `Invalid target_commitish` — a message that
  names neither the branch nor the release object. Pinning makes a release depend only
  on the tag it was pushed for.
- **Verify a release by its jobs, not the run's conclusion.** A red run can still have
  published (docs failing after publish succeeded), and a green-looking release can
  have skipped publish because an upstream job failed. Check the publish job's own
  status and confirm the artifact exists on the registry.
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

## Meta files (skeletons in `$REFERENCES_OVERLAY_ROOT/references/`; language doc adds specifics on top)
- `README.md`, `.gitignore`: from `references/`. `README.md` is the package
  long-description (`readme=` in the manifest) and MUST ship in the built package
  (sdist + wheel). `.gitignore` opens with the root-anchored category rule
  `/.*` + `!/.gitignore` + `!/.gitattributes` (re-include `!/.github/` only when
  CI exists) — a category beats a list, same philosophy as Cargo's
  `exclude = [".*"]` (decided 2026-08-02, first applied in one repo).
  It additionally keeps `.agents` (slashless — `dotagents link` makes it a
  symlink, which a directory-only `.agents/` won't match), `CLAUDE*`, `.claude`
  for nested occurrences the root-anchored rule can't reach, plus the
  language's build output.
- `AGENTS.md` — three distinct roles; keep them separate, never merge:
  - **Shipped API header** (committed): the public API header-file-style (exports
    with signatures/args/defaults, return-or-contract, env vars, gotchas) so a
    consuming agent skips the source. Current with the API, same commit. **Placement
    follows the packaging boundary, not a fixed path** — at the root of whatever the
    packaging tool actually ships, beside the human `README.md`; verify with the
    tool's list command (`cargo package --list`, `npm pack --dry-run`, inspect the
    built wheel). Python: `src/<pkg>/AGENTS.md` (a repo-root file is not in the
    wheel). Rust: `<crate>/AGENTS.md` (the crate root *is* the shipped root).
    Per-module headers below that root suit large surfaces. Must be
    **self-contained** — no repo-relative links; an installed consumer has no repo.
  - **Repo-root `AGENTS.md`** (committed, expected by default — decided
    2026-08-02): repo-level orientation — layout, cross-cutting rules, build/test
    commands, environments, CI, release. The split mirrors Python's convention:
    repo info at the root, per-package/crate API info in each package's own
    header (a multi-crate workspace shows the per-crate shape). Not the API header, and it does
    not ship; it may link freely within the repo but never into `.agents/`.
  - **`.agents/AGENTS.md`** (gitignored): private working notes — plans, hosts,
    session history. Never cited from anything tracked.
- **`*.local.*` anywhere: never committed, never packaged.** These are personal
  overrides — one developer's or one machine's, not the team's — and the core
  `AGENTS.md` already defines `.local` as *"never committed or copied into a repo,
  plan, or shared config"*. That rule has two teeth, and both must be fitted
  explicitly because neither is automatic:
  - **`.gitignore`**: add `*.local.*`. Without it the first `AGENTS.local.md`
    someone writes is a tracked file.
  - **The manifest's exclude list**: add `*.local.*`. A dotfile pattern such as
    Cargo's `exclude = [".*"]` does **not** match `AGENTS.local.md` or
    `config.local.toml` — the name does not start with a dot. Verified by
    creating both in a crate and running `cargo package --list`: both appeared
    in the shipping set.

  **`AGENTS.local.md` is the dangerous one**, because it sits in the same
  directory as an `AGENTS.md` that is *supposed* to ship. Same directory,
  nearly the same name, opposite destinations: the API header is a deliverable,
  the `.local` override must never leave the machine. Any rule that treats them
  as one family gets one of the two wrong — which is why `.local` is excluded by
  its own pattern rather than by anything that also matches `AGENTS.md`.

  The failure is quiet and one-way: a personal override carries local paths,
  hostnames, and sometimes credentials, and a published artifact cannot be
  recalled. Check with the packaging tool's own list command, not by reading
  the pattern and assuming. The check that matters is a pair — confirm
  `AGENTS.md` IS listed and `AGENTS.local.md` is NOT, in the same run.
- `CHANGELOG.md`: Keep a Changelog format (`references/CHANGELOG.md`) — `[Unreleased]`
  always at top, one `## [x.y.z] - <date>` heading per release. Release is the last
  gate for your personal leak-scanning command (a personal command module in your
  private `.agents/`, not part of dotagents), not the only one — it runs at every
  publishing handoff (EXEC.md). It scans the **working tree** — tracked AND
  untracked-but-not-ignored, since a file an agent has just written is where a fresh
  leak actually is — for private-plan leaks (`.agents/` paths, plan basenames,
  `Phase N` phrasing) AND commit messages for agent-attribution trailers/URLs
  (`Co-Authored-By:` naming a model, any `*-Session:` trailer such as
  `Claude-Session:`, `claude.ai/code/session`). Those are auto-added by the harness
  and must be stripped before pushing public — the link exposes a session id.
  **Judge it by its exit code, and read the header it prints.** 0 pass, 1 hits, 2
  usage, **3 = it could not check** — not a repo, or nothing readable. The header
  names the enumeration mode and the pattern counts, and an empty pattern class is a
  banner, not a silent pass: a `PASS` that checked nothing used to be indistinguishable
  from a clean tree. Never pipe it to `tail`; the pipe returns tail's status and the
  verdict is lost.
  If one already landed, rewrite it out
  (`git filter-branch --msg-filter "sed '/^Claude-Session:/d'"`); pre-rewrite SHAs stay
  reachable on the host until it GCs.
  **Content — an entry states what changed and what a user must do about it**
  (D12). Keep: the change in plain terms with the
  affected names; old vs. new behaviour for a fix or break; migration (old → new); and
  concrete data that makes it legible (measured numbers, versions, real values). Cut:
  justifications for the version number (D13), how the work was done/found/tested,
  design intent and future plans ("deliberately", "could be"), editorial framing
  ("worth noticing", asides that comment rather than inform), and any reference to the
  conversation, the plan, or the sequence of work. The test: *would this sentence make
  sense to someone who has never spoken to me, reading it in a year?* If it only makes
  sense as a reply to something, it belongs in the commit message. Facts that *caused*
  a change are not commentary — "TrueNAS 26.0 ships `requests`, `pyyaml`" is data;
  "I probed the box to find out" is process.
- `RELEASENOTES.md` (repos with a perf story): the detailed companion to a terse
  `CHANGELOG.md` — the durable audit trail for perf claims and release decisions. Per
  release: a **previous→current benchmark table** (median) from `benchmarks/results/`;
  **migration nuance** too detailed for Keep a Changelog; **benchmark caveats** (runner
  noise, OS, language version, local-vs-CI); **validation evidence** (tests/build/docs/
  leak-check, CI run IDs); **publication state** (prepared / main pushed / tag awaiting
  per-release consent). `[Unreleased]` records the next perf target so a regression is
  caught before tagging. Same leak rules as public notes.
- `LICENSE`: **none by default** (decided 2026-08-02). A new repo gets no
  license until the user explicitly picks one — unlicensed means all rights
  reserved. Consequences while unset: the repo must **never** be pushed to a
  public remote or published to any public registry; manifests carry no
  `license` field (an SPDX claim would be false) and are marked non-publishable
  (Cargo `publish = false`, npm `"private": true`, etc. — registries would
  reject or mislicense them anyway). When the user does pick MIT,
  `references/LICENSE` is the skeleton (fill `<year>`/`<copyright_holder>`) and
  the publish guards lift.
- Template files may contain hidden `<!-- EXECUTOR: ... -->` comments — strip them
  before writing real repo files.

## Versioning
SemVer for git tags and `CHANGELOG.md`. The package manifest's own version syntax may
legitimately differ (the language doc says how, e.g. PEP 440) — never "fix" one to
match the other.

**Pre-1.0 (`0.y.z`), the minor slot is the compatibility promise.** SemVer itself
says anything goes at `0.x`, which is not a policy — this is the policy:

- **Bump the MINOR (`0.y+1.0`) only to break the documented API.** "Documented"
  is the whole test: a change to behaviour a consumer was told to rely on —
  removing or renaming a public method, changing a signature or a documented
  default, changing what a documented method does. Nothing else earns a minor.
- **Everything else is a PATCH (`0.y.z+1`)** — new methods, new optional
  kwargs, new modules, bug fixes, and any change to *undocumented* behaviour
  (an accident of implementation, an internal helper, a bug someone was
  relying on). **Additive API is a patch.** This is the part that differs from
  post-1.0 SemVer, where new functionality would be a minor.
- **Why**: it makes a minor range a real subscription. A consumer pinning
  `~=0.9.0` / `^0.9` gets every fix and every addition without re-reading the
  changelog, and a minor bump becomes a genuine "stop, read this" signal
  instead of routine noise. Bumping the minor for additions spends that signal
  on changes that could never break anyone, and the range stops meaning
  anything.
- A release adding three public methods is therefore **`0.9.0` → `0.9.1`**, not
  `0.10.0`. If it also removed a documented method, it would be `0.10.0`
  regardless of how small the removal was.
- This is a *pre-1.0* rule. At `1.0.0` and beyond, ordinary SemVer resumes:
  additive functionality is a minor, breaks are a major.

Same discipline as the release-consent rule in
`$ENGINEERING_OVERLAY_ROOT/engineering/rules.md`: the user names the version or
release type, and a change set arguing for a different one is worth *one question
before acting*, never a correction afterwards. Under this policy the answer to "these
are new features, shouldn't it be a minor?" is **no** — check the documented API for
breaks instead.

Per-language CI templates (opt-in examples): `$<LANG>_OVERLAY_ROOT/references/workflows/<lang>/`.
