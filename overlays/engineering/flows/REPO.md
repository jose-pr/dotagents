# Flow: Repository Standard (language-agnostic)

Applies when creating a repo or bringing one up to standard: a gap against this list
is flagged or fixed, never silently skipped. `$<LANG>_OVERLAY_ROOT/kb/<LANG>.md`, if
it exists, supplies the concrete toolchain and may override specifics; neither file
restates the other.

## Layout
- Source in its own directory (the language doc names it, e.g. `src/`), separate
  from tests, docs, examples and benchmarks.
- `tests/`: a real suite, runnable by one documented command.
- `benchmarks/`: a dedicated perf suite, run on demand, never as per-push CI. Its
  runner (`--save`) writes one JSON result per (version, interpreter) to
  `benchmarks/results/<name>.json`; each metric reports min/median/max ms per call
  over N samples, compared on the median (`dotagents compare-bench`). Results are
  **tracked and committed**, which is what keeps a before/after recoverable (never
  under the gitignored `.agents/`). `benchmarks/README.md` documents the schema and
  the reproduce command; keep a baseline entry for the pre-optimization state.
- `examples/`: runnable scripts and configs, not scratch files.
- `docs/` + a docs site: a hand-written landing page (never a verbatim `README.md`
  embed: its relative links break from a docs-site root;
  `$ENGINEERING_OVERLAY_ROOT/references/docs-index.md`) plus generated API reference
  where the ecosystem supports it (the language doc names the generator and whether
  a deploy step is needed).

## CI — three workflow files, one per concern (test, release, docs)
A release must never be the first time a config runs, testing must never need a
release, and the docs site must be redeployable between releases.
- **Test workflow**: `workflow_dispatch` (with a `ref` input) + a throwaway `ci-*`
  tag trigger, or a `ci-*` branch where tags cannot be pushed (a cloud git proxy),
  deleted after the run like the tag. Not necessarily every push or PR. Agents may
  push `ci-*` without asking: use unique names (`ci-<topic>-<timestamp>`), poll the
  run to completion, report the result, then delete the tag or branch locally and
  on the remote. Never leave one behind.
- **Release workflow** (`v*` tag): test gate → build → publish, in dependency order,
  plus a **docs-gate** job that builds the docs strictly but does **not** deploy.
  Publish steps are re-run-safe (e.g. `skip-existing` on the registry upload).
  Prefer the registry's OIDC / trusted publishing over stored long-lived tokens.
- **Docs workflow** owns **all** Pages deploys, on two triggers: a push to the
  default branch touching docs sources, and `workflow_dispatch` for any ref. The
  release workflow gates on the strict docs build, then dispatches this workflow at
  its tag; it does not listen for `release: published`, because a release created
  with `GITHUB_TOKEN` starts no other workflow. The docs site is a required
  deliverable. Ecosystems that host API docs externally (e.g. docs.rs) need this
  workflow only for a narrative guide.
- **GitHub Pages: settings live in the workflow, not in repo settings clicked once.**
  A docs job that assumes pre-configured state fails on a fresh clone or fork, and
  inside the release workflow turns a good release red.
  - **Self-enable Pages** in every workflow that builds the docs, identically:
    `actions/configure-pages` with `enablement: true` and `permissions: pages: write`
    on that job. It also sets Pages to **build from GitHub Actions**. Without it the
    step fails with `Get Pages site failed ... Not Found`, naming the REST endpoint,
    not the setting. A `docs.yml` that self-enables beside a release workflow that
    does not hides the bug until a release.
  - **Deploy job**: `permissions: pages: write` + `id-token: write` and
    `environment: name: github-pages`. Schema linters flag `github-pages` as an
    invalid environment; it is a false positive (GitHub reserves the name).
  - **Never gate publish on the docs *deploy*.** The strict docs *build* gates the
    release; the deploy stays off the build → release → publish chain.
  - **Repo settings to check first** (their errors name an API endpoint, not the
    setting):
    - *Default workflow permissions.* A job that does not declare
      `permissions: pages: write` needs them to be write (Settings → Actions →
      General, or
      `gh api -X PUT repos/<org>/<repo>/actions/permissions/workflow -f default_workflow_permissions=write`);
      without it `configure-pages` fails with `Resource not accessible by
      integration`. Jobs that declare it (as the templates do) work under read. If
      enabling still fails, enable Pages once with your own credentials:
      `gh api -X POST repos/<org>/<repo>/pages -f build_type=workflow`.
    - *The `github-pages` environment's deployment branch policy must allow the
      tag.* It defaults to the default branch, so a docs deploy from a `v*` tag is
      rejected after a successful build (`Tag "vX.Y.Z" is not allowed to deploy to
      github-pages due to environment protection rules`). Add a tag policy:
      `gh api -X POST repos/<org>/<repo>/environments/github-pages/deployment-branch-policies -f name='v*' -f type=tag`.
- **Pin release objects to the tagged commit**: pass the release action
  `target_commitish: ${{ github.sha }}`. Unset, it defaults to the default branch,
  and after a branch rename every release fails at note generation with
  `Invalid target_commitish`, a message naming neither the branch nor the release.
- **Verify a release by its jobs, not the run's conclusion.** A red run can still
  have published, and a green-looking one can have skipped publish. Check the
  publish job's own status and that the artifact exists on the registry.
- **Changelog-derived release notes**: without a PR flow, auto-generated "What's
  Changed" notes are thin, so scrape the pushed tag's `CHANGELOG.md` section into
  the release body (keep the platform's compare-link addendum). `ubuntu-latest`
  bundles Python 3, so every language reuses the same `shell: python` scraper step.
  Public notes describe behaviour and rationale, never plan names, `.agents/` paths,
  reviewer ids or `Phase N` labels (`$ENGINEERING_OVERLAY_ROOT/kb/LEAKS.md`).
- **Tagging**: the `v*` push triggers everything and publish is irreversible
  (registries never reuse a version). Finish every other step first so tagging is
  the only thing left, then ask, per release (the Releases rule in
  `$ENGINEERING_OVERLAY_ROOT/rules/ENGINEERING.md`).

## Meta files (skeletons in `$ENGINEERING_OVERLAY_ROOT/references/`; language doc adds specifics on top)
- `README.md`: from `references/`. It is the package long-description (`readme=` in
  the manifest) and MUST ship in the built package (sdist + wheel). **Every README
  has the template's shape**: its badge row in its order, its `##` sections with its
  spelling in its order; fill it, never reorder or rename. The language doc supplies
  the registry badges and their place in the row. Links are absolute: the README is
  rendered on the registry, where a relative link breaks.
- `.gitignore`: from `references/`. It opens with the root-anchored category rule
  `/.*` + `!/.gitignore` + `!/.gitattributes` (re-include `!/.github/` only when CI
  exists); a category beats a list, as Cargo's `exclude = [".*"]` does. It also
  keeps `.agents` (slashless: `dotagents link-project` can make it a symlink, which
  a directory-only `.agents/` won't match), `CLAUDE*` and `.claude` for nested
  copies the root rule can't reach, plus the language's build output.
- `.gitattributes`: from `references/` (`* text=auto eol=lf`; exceptions and
  `dotagents eol` in `$ENGINEERING_OVERLAY_ROOT/kb/EOL.md`), created WITH the repo,
  and added to an existing repo that lacks it before you edit it. Verify git sees
  it: `git status --porcelain --ignored -- .gitattributes` prints `??`, `A` or `M`,
  never `!!` (a `.*` ignore without `!/.gitattributes` swallows it; `git
  check-ignore`'s exit code cannot tell an ignore from a negation).
- `AGENTS.md`, three roles, never merged:
  - **Shipped API header** (committed): the public API header-file-style (exports
    with signatures/args/defaults, return-or-contract, env vars, gotchas) so a
    consuming agent skips the source; current with the API, same commit. **Placed by
    the packaging boundary, not a fixed path**: at the root of what the packaging
    tool ships, beside `README.md`; verify with its list command (`cargo package
    --list`, `npm pack --dry-run`, inspect the built wheel). Python
    `src/<pkg>/AGENTS.md` (a repo-root file is not in the wheel); Rust
    `<crate>/AGENTS.md`. Per-module headers below that root suit large surfaces.
    **Self-contained**: no repo-relative links; an installed consumer has no repo.
  - **Repo-root `AGENTS.md`** (committed, optional): orientation (layout,
    cross-cutting rules, build/test commands, environments, CI, release). Repo info
    at the root, per-package API info in each package's header. It does not ship;
    it may link within the repo but never into `.agents/`. A committed `AGENTS.md`
    in a subdirectory is this kind, scoped to that subtree.
  - **`.agents/AGENTS.md`** (gitignored): private working knowledge, never cited
    from anything tracked.

  Every `AGENTS.md` describes its own directory: a parent indexes a child file
  (name, one line, path) and never restates it; each fact has one home. A file read
  before every task fits in about 150 lines; past that, move its largest topic down
  into a routed file.
- **`*.local.*` anywhere: never committed, never packaged.** These are one person's
  or one machine's overrides. Fit both guards explicitly; neither is automatic:
  - **`.gitignore`**: `*.local.*`, or the first `AGENTS.local.md` is tracked.
  - **The manifest's exclude list**: `*.local.*`. A dotfile pattern such as Cargo's
    `exclude = [".*"]` does **not** match `AGENTS.local.md` or `config.local.toml`.

  `AGENTS.local.md` sits beside an `AGENTS.md` that is *supposed* to ship, so
  exclude it by its own pattern, never by one that also matches `AGENTS.md`. A
  published override (local paths, hostnames, sometimes credentials) cannot be
  recalled. Check with the packaging tool's list command, as a pair in one run:
  `AGENTS.md` IS listed, `AGENTS.local.md` is NOT.
- `CHANGELOG.md`: Keep a Changelog format (`references/CHANGELOG.md`): `[Unreleased]`
  always on top, one `## [x.y.z] - <date>` heading per release. Release is the last
  gate for the leak scan, not the only one (`dotagents leak-check <repo>`;
  `$ENGINEERING_OVERLAY_ROOT/kb/LEAKS.md`). An attribution trailer that already
  landed is rewritten out (`git filter-branch --msg-filter "sed
  '/^Claude-Session:/d'"`); pre-rewrite SHAs stay reachable on the host until it GCs.
  **An entry states what changed and what a user must do about it.** Keep: the
  change in plain terms with the affected names; old vs. new behaviour for a fix or
  break; migration (old → new); concrete data (measured numbers, versions, real
  values). Cut: justifications for the version number, how the work was done, found
  or tested, design intent and future plans ("deliberately", "could be"), editorial
  asides, and any reference to the conversation, the plan or the sequence of work.
  The test: *would this sentence make sense to someone who has never spoken to me,
  reading it in a year?* If it only makes sense as a reply, it belongs in the commit
  message. Facts that *caused* a change are data ("the distribution's Python ships
  `requests`"); how they were found is process.
- `RELEASENOTES.md` (repos with a perf story): the detailed companion to a terse
  `CHANGELOG.md`, the audit trail for perf claims and release decisions. Per
  release: a **previous→current benchmark table** (median) from
  `benchmarks/results/`; **migration nuance** too detailed for the changelog;
  **benchmark caveats** (runner noise, OS, language version, local vs. CI);
  **validation evidence** (tests, build, docs, leak-check, CI run IDs); **publication
  state** (prepared / main pushed / tag awaiting consent). `[Unreleased]` records
  the next perf target so a regression is caught before tagging. Same leak rules as
  public notes.
- `LICENSE`: **none by default.** A new repo gets no license until the user picks
  one; unlicensed means all rights reserved. While unset, the repo is **never**
  pushed to a public remote or published to a public registry; manifests carry no
  `license` field and are marked non-publishable (Cargo `publish = false`, npm
  `"private": true`, etc.). When the user picks MIT, `references/LICENSE` is the
  skeleton (fill `<year>`/`<copyright_holder>`) and the publish guards lift.
- Template files may carry hidden `<!-- EXECUTOR: ... -->` comments: strip them
  before writing real repo files.

## Versioning
SemVer for git tags and `CHANGELOG.md`. The manifest's own version syntax may differ
(the language doc says how, e.g. PEP 440); never "fix" one to match the other.

**Pre-1.0 (`0.y.z`), the minor slot is the compatibility promise:**

- **Bump the MINOR (`0.y+1.0`) only to break the documented API**: behaviour a
  consumer was told to rely on (removing or renaming a public method, changing a
  signature, a documented default, or what a documented method does).
- **Everything else is a PATCH (`0.y.z+1`)**: new methods, new optional kwargs, new
  modules, bug fixes, and any change to *undocumented* behaviour. **Additive API is
  a patch**, unlike post-1.0 SemVer.
- **Why**: a minor range becomes a real subscription. A consumer pinning `~=0.9.0` /
  `^0.9` gets every fix and addition without re-reading the changelog, and a minor
  bump stays a genuine "stop, read this" signal.
- Three new public methods: **`0.9.0` → `0.9.1`**, not `0.10.0`. Also removing a
  documented method, however small: `0.10.0`.
- From `1.0.0`, ordinary SemVer resumes: additive is a minor, breaks are a major.

The user names the version or release type (the Releases rule in
`$ENGINEERING_OVERLAY_ROOT/rules/ENGINEERING.md`). Under this policy the answer to
"these are new features, shouldn't it be a minor?" is **no**: check the documented
API for breaks instead.

Per-language CI templates (opt-in examples): `$<LANG>_OVERLAY_ROOT/references/workflows/<lang>/`.
