# Consolidate ~/.agents onto the private .agents repo

Status: draft
Executor: code-executor/implemented — multi-repo git surgery with a destructive
final step; needs judgment on conflict resolution, not mechanical edits.

## Goal

Make `~/.agents` **be** a clone of the private `jose-pr/.agents` repo, so global
config + per-project private agent state sync across devices/envs. Get there by
merging three diverged sources, keeping the newest of each, losing nothing.

## Known Facts & Context

Verified 2026-07-20 by inspection of all three sources.

### The three sources

| Source | What it is | State |
| --- | --- | --- |
| `~/.agents/` | Install target, **not a git repo** (no `.git`, no remote) | Newest global config (D37–D40 line) + 22 local findings + `AGENTS.local.md` + `dotagents/log.md` |
| `jose-pr/.agents` (private, GitHub) | The intended sync repo. Created 2026-07-19, last push 2026-07-20, 17 commits, `main` | Has `hooks/`, `kb/PRIVATE_SYNC.md`, `kb/CLOUD_ENV.md`, `projects/{glyphive,pkgforge,user}/`. Global config is **stale** (pre-D37–D40) |
| the public `dotagents` checkout | Public source repo for the config + CLI | Local `main` **diverged: 5 ahead, 22 behind** `origin/main` |

Scratch clone of the private repo already at:
`<scratchpad>/agents_upstream`
(Re-clone if stale: `git clone https://github.com/jose-pr/.agents.git <dest>`.)

### Git divergence in `devel/dotagents` (the root cause)

Fork point: `52e5983`. `git fetch origin` already run.

- **`origin/main` only (22 commits)** — the whole private-sync feature set, released
  as **0.2.0** (`2f4678c`): `6cedfcb` link/sync, `e51be54` private-sync overlay,
  `e7b60b8` `tools/cloud-setup.sh`, `ab15335`+`12e810d` token auth + proxy-rewrite
  bypass, `25373a6` sync self-auth, `10e3d1c`/`5626c95`/`889d36b` cloud-setup
  hardening, `9dc8434`+`64dee04` **duho 0.3.3 migration** (Args/Cmd split + zipapp
  shim), `6dcad17`+`9014d50` leak_check session-trailer gates, `427fe33` the fix for
  the `link-adopts-harness-source-clone` finding.
- **local `main` only (5 commits)** — `86d70fd` duho **0.2.0** entrypoint adapt,
  `d22fc13` D37–D39 executor routing, `5bb7bd3`+`78457a7` D40, `c313028` leak_check
  bare-`AGENTS.md` false-positive fix.

**Conflict — duho, one-sided.** Local pins `duho>=0.2.0` and `86d70fd` adapts
`cli.py` in 10 lines to the 0.2.0 `__run__`→`__call__` entrypoint. Remote pins
`>=0.3.3` and `9dc8434` does the full Args/Cmd migration, which **supersedes** it and
is what 0.2.0 shipped against. Resolution: take **remote** for `pyproject.toml` and
`src/dotagents/cli.py`; drop local `86d70fd`.

**Conflict — D-numbers.** Local D37–D40 and remote D37–D43 are *different* decisions
sharing numbers. Remote occupies D01–D43 (plus `D33-log-split.md`). Local's four must
renumber to **D44–D47**:

| Local (old) | Title | New |
| --- | --- | --- |
| D37 | role-based multi-provider executor routing | D44 |
| D38 | host-native provider routing | D45 |
| D39 | practical GPT-5.6 task routing | D46 |
| D40 | root AGENTS.md is the committed public library-interface doc | D47 |

Remote D37–D43 keep their numbers (private-sync, duho 0.3.3, cloud-setup self-heal,
sync self-auth, leak_check trailers, recovery-hook branch, link/sync git-checkout
guard).

Non-conflicting: `tools/leak_check.py` is touched by both for unrelated reasons
(local: bare-`AGENTS.md` false positive; remote: session-trailer gates) — merges
cleanly. All other local-only files (`overlays/flows/*`, `overlays/python/kb/`,
`overlays/references/*`, `src/dotagents/_overlay/AGENTS.md`) are untouched by remote.

### Content divergence: installed `~/.agents` vs private repo

- **Newer in `~/.agents`** (private repo predates D37–D40): `AGENTS.md`,
  `flows/{EXEC,PLAN,REPO,REVIEW}.md`, `MODELS.md` (6101b vs 3326b).
  Note `overlays/flows/flows/{EXEC,PLAN}.md` are *smaller* than installed — that is
  the deliberate token-budget compression (`.agents/dotagents/NOTES.md`), not loss.
- **Only in private repo**: `hooks/` (4 files), `kb/PRIVATE_SYNC.md`,
  `kb/CLOUD_ENV.md`, `README.md`, `CLAUDE.md`, `projects/` (glyphive has a real
  8-file `codec_review` plan tree + prototypes), `dotagents/DECISIONS.md`,
  3 findings.
- **Only in `~/.agents`**: `AGENTS.local.md` (8898b, machine-local — must stay
  untracked), `dotagents/log.md`, 22 findings under `dotagents/findings/`
  (13 open + 9 processed), `kb/RECOVERY.md`, `kb/python.md`, `examples/**`,
  `references/**`, `tools/` (4 scripts).
- Private repo has **no `.gitignore`**.

### Environment

- Venv: `$VENV` (`dotagents` installed
  editable from this checkout, version 0.1.0).
- **duho 0.2.0 is currently installed** — the 0.3.3 pin needs a reinstall before the
  CLI runs post-merge.
- A second worktree exists: `.claude/worktrees/agent-a8be6941dda4bcc1f` — ignore.

### Token rotation — resolved, no longer gating

`DOTAGENTS_AGENTS_TOKEN` was printed into a transcript twice (2026-07-19 and
2026-07-20, both via a first-look `env` triage). **Both are resolved: the owner has
rotated the PAT and updated the environment secret**; the disclosed tokens are
revoked. Both notes are archived under `dotagents/findings/processed/`
(`rotate-agents-token.md`, `rotate-agents-token-2.md`) — done as part of this work,
so the scratch clone already carries the move; it lands upstream with Phase 4.

The surviving carry-forward is the *prevention* gap, which is Phase 2: the "never
print `DOTAGENTS_*` values" guard lives only in `kb/CLOUD_ENV.md`, loaded on demand —
after the triage that caused both leaks.

## Phases

### Phase 1 — Reconcile the `devel/dotagents` git divergence

Goal: local `main` contains all 22 remote commits + the 4 local content commits,
with duho at 0.3.3 and decisions renumbered.

Files: `pyproject.toml`, `src/dotagents/cli.py`,
`.agents/dotagents/decisions/D3{7,8,9}.md`+`D40.md` → `D4{4,5,6,7}.md`,
`.agents/dotagents/DECISIONS.md`, `tools/leak_check.py`.

Guidance:
- Branch first: `git switch -c merge/private-sync-reconcile` — do **not** operate on
  `main` directly. *Why:* the merge is nontrivial and must be abandonable.
- `git merge origin/main`. Take **remote** wholesale for `pyproject.toml` and
  `src/dotagents/cli.py` (`git checkout --theirs`), then verify local `86d70fd`'s
  intent is genuinely covered by the 0.3.3 migration — read `cli.py` after merging;
  every command class must be `(LoggingArgs, Cmd)` with `__call__`.
- Rename local D37–D40 → D44–D47 **with `git mv`**, rewrite each file's `# D<n>:`
  heading and any internal cross-refs, then merge both `DECISIONS.md` index tails so
  D01–D47 are listed once each in order. *Why:* remote's D37–D43 are already
  published in the public repo's history; renumbering *those* would break references.
- Grep the whole tree for stale `D37`–`D40` references pointing at the local
  meanings (`overlays/`, `src/dotagents/_overlay/AGENTS.md`, `NOTES.md`, plans) and
  repoint to D44–D47. *Why:* `_overlay/AGENTS.md` carries `[D40]` markers inline.
- Reinstall after the pin change:
  `$VENV\Scripts\python.exe -m pip install -e .`

Done when: `git merge` is committed with no conflict markers anywhere
(`git grep -nE '^(<<<<<<<|=======|>>>>>>>)'` is empty); `D01`–`D47` each appear
exactly once in `DECISIONS.md`; `pip show duho` reports ≥0.3.3.

### Phase 2 — Add the `AGENTS.md` always-on secret-printing guard

Goal: close the prevention gap behind both token leaks.

Files: `src/dotagents/_overlay/AGENTS.md` (Leakage rule), new
`.agents/dotagents/decisions/D48.md`, `.agents/dotagents/DECISIONS.md`.

Guidance:
- Add one line to the **always-on** Leakage rule: never print or echo `DOTAGENTS_*`
  environment values (no bare `env`, `printenv`, or `echo $DOTAGENTS_*`); to check
  presence, test emptiness without printing. *Why:* the guard currently lives only
  in `kb/CLOUD_ENV.md`, which loads on demand — *after* the first-look `env` triage
  that caused both leaks.
- Keep it to one line; `AGENTS.md` core is token-budgeted (~2743b, see `NOTES.md`).
- Record as D48 and add the index row.

Done when: the rule is in the always-on section (not "Load on demand"), core
`AGENTS.md` has not grown by more than ~200 bytes, and D48 is indexed.

### Phase 3 — Verify the public repo

Goal: the source repo is green before it becomes the merge input.

Guidance — run all three, all must PASS (per repo `AGENTS.md`):
```
$VENV\Scripts\python.exe tools/audit_config.py --root .
$VENV\Scripts\python.exe tools/audit_config.py --check-templates --root .
$VENV\Scripts\python.exe tools/audit_config.py --repo-hygiene .
```
Then smoke-test the merged CLI actually loads under duho 0.3.3:
`...\Scripts\dotagents.exe --help` and `...\Scripts\dotagents.exe link --help`.
*Why:* Phase 1 took remote's `cli.py` wholesale against a locally-changed tree;
`--help` under the zipapp shim is the documented fragile surface (`64dee04`).

Done when: three audits PASS; `dotagents --help` lists `init`, `install`, `audit`,
`build-pyz`, `link`, `sync`.

### Phase 4 — Merge content into the private `.agents` repo

Goal: the private repo holds the newest of every file, plus a `.gitignore`. Still
non-destructive to `~/.agents`.

Work in a **fresh clone** in the scratchpad, never in `~/.agents` yet.

Guidance:
- Overwrite from the reconciled public repo (public is newer): `AGENTS.md`,
  `MODELS.md`, `flows/*`. Take them from the **install output**, not the overlay
  sources — i.e. run `install` into a temp dir and copy from there, so the private
  repo receives assembled files. *Why:* `overlays/flows/flows/*.md` are overlay
  inputs; `~/.agents/flows/*` are the assembled result.
- Preserve `AGENTS.md`'s `<!-- dotagents:begin -->`/`<!-- dotagents:end -->` markers
  — the private repo's copy has them, `~/.agents`'s does not. The managed block must
  stay delimited so `init` can refresh it (`src/dotagents/_merge.py`).
- Keep private-repo-only content untouched: `hooks/`, `kb/PRIVATE_SYNC.md`,
  `kb/CLOUD_ENV.md`, `projects/`, `README.md`, `CLAUDE.md`.
- Copy in from `~/.agents` what the private repo lacks: `dotagents/log.md`, all 22
  `dotagents/findings/**`, `kb/RECOVERY.md`, `kb/python.md`, `examples/**`,
  `references/**`, `tools/**`. Merge `dotagents/DECISIONS.md` with the reconciled
  D01–D48 index.
- **Do not copy `AGENTS.local.md`.**
- Add `.gitignore` at the private repo root:
  ```
  *.local.*
  AGENTS.local.md
  .venv/
  __pycache__/
  ```
  *Why:* `*.local.*` alone does not match `AGENTS.local.md` — the basename has no
  trailing segment after `local`. Both lines are needed.
- Commit in logical commits (repo git rule): config-update / private-content-import /
  gitignore separately. Do **not** push yet.

Done when: the scratch clone has no file that is older than its counterpart in either
source; `git status` clean; `git check-ignore -v AGENTS.local.md` matches.

### Phase 5 — Cut `~/.agents` over to the clone

Goal: `~/.agents` **is** the private repo. **Destructive — gated.**

Guidance:
- **Stop and confirm with the user before this phase** — it is destructive and it
  pushes. (Token rotation is already done; it is no longer a precondition.)
- Back up first: copy `~/.agents` to
  `~/.agents.bak-<YYYYMMDD>` and verify the copy is complete and readable *before*
  removing anything. *Why:* `AGENTS.local.md`, `log.md`, and 22 findings exist
  **only** here — an incomplete backup is unrecoverable.
- Push the Phase-4 branch, then replace `~/.agents` with a clone of it.
- Restore `AGENTS.local.md` from the backup into the clone (untracked, ignored).
- Verify `~/.agents/.git` exists, `git status` is clean, `AGENTS.local.md` is present
  and ignored, and a session-start read of `~/.agents/AGENTS.md` still resolves.
- Keep the backup until Phase 6 passes.

Done when: `git -C ~/.agents status` is clean with `origin` =
`https://github.com/jose-pr/.agents.git`; `AGENTS.local.md` present + ignored; every
file from the pre-cutover inventory accounted for.

### Phase 6 — Prove the cross-device loop

Goal: the stated goal — sync across devices/envs — actually works.

Guidance:
- `dotagents link .` in this repo, confirm `<project>/.agents` resolves to
  `~/.agents/projects/dotagents/` and that `.gitignore` excludes `.agents`
  (slashless — `1c9bf7c`; a directory-only `.agents/` pattern does not match a
  symlink).
- Round-trip: write a marker file through the link, `dotagents sync`, confirm it
  lands in the private repo; clone the repo to a scratch dir and confirm the marker
  is there.
- Confirm `dotagents link` refuses to adopt a `.agents` that is itself a git checkout
  (D43 / `427fe33`) — the `link-adopts-harness-source-clone` finding. If it holds,
  move that finding to `dotagents/findings/processed/` with a resolution note.
- Delete the marker and sync again.

Done when: the marker round-trips through the remote and is then cleanly removed;
`git -C ~/.agents status` clean; the D43 guard is confirmed by observation.

## Verification

```
cd <dotagents-checkout>
$VENV\Scripts\python.exe tools/audit_config.py --root .
$VENV\Scripts\python.exe tools/audit_config.py --check-templates --root .
$VENV\Scripts\python.exe tools/audit_config.py --repo-hygiene .
$VENV\Scripts\dotagents.exe --help
git -C <dotagents-checkout> grep -nE "^(<<<<<<<|=======|>>>>>>>)"
git -C %USERPROFILE%\.agents status --short
git -C %USERPROFILE%\.agents check-ignore -v AGENTS.local.md
```

Expected: three audits PASS; `--help` lists all six subcommands including
`link`/`sync`; the conflict-marker grep prints nothing (exit 1); `~/.agents` status
clean; `AGENTS.local.md` reported as ignored by `.gitignore`.

## Reporting

Update `## Progress` live, one phase at a time — never batch. Gotchas found during
execution go to this repo's root `AGENTS.md` (or `.agents/dotagents/NOTES.md` for
config-wording facts). New decisions continue at **D49+**.

## Progress

- [ ] Phase 1 — reconcile the `devel/dotagents` git divergence
- [ ] Phase 2 — always-on secret-printing guard (D48)
- [ ] Phase 3 — verify the public repo (3 audits + CLI smoke)
- [ ] Phase 4 — merge content into the private `.agents` repo
- [ ] Phase 5 — cut `~/.agents` over to the clone **(destructive; user-gated)**
- [ ] Phase 6 — prove the cross-device loop
