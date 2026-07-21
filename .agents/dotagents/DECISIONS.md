# Config Design Decisions — index

Not loaded in normal sessions. Read when iterating on the config (routing: 'Asked to
iterate on this global config'). One file per decision under `decisions/`; open only
what you need. Design context (goals, cost model, review protocol, size mapping) is in
[NOTES.md](NOTES.md). Public + sanitized: describe private repos/plans by role, never name.

## How to iterate
1. Triage `~/.agents/dotagents/findings/` (D25/D28): each note → keep as a decision here
   (+ overlay/flow change) or drop; **move** folded notes to `findings/processed/`.
2. Read this index + the current core + the flow file(s) you're changing.
3. Audit vs reality: check a recent completed plan for where executors deviated/re-derived.
4. New decision → new `decisions/D<nn>.md` + an index line here; edit the base overlay or an overlay; reinstall
   (`python install.py`). Keep core ≤ ~60 lines; conditional detail → a flow file.
5. Never let a flow file restate another's content — link it. Add provenance on incidents.

## Decisions

- [D01](decisions/D01.md) — core+flows split: AGENTS.md keeps only every-session rules + routing table, task rules live in flows/
- [D02](decisions/D02.md) — release-tag consent stays in the core, not gated behind loading REPO.md
- [D03](decisions/D03.md) — plain paths, no file:/// links anywhere in the config
- [D04](decisions/D04.md) — kb files point at flows/REPO.md for the repo standard; stale examples/ links fixed to references/
- [D05](decisions/D05.md) — plans carry a Status line; executors refuse anything not ready/executing
- [D06](decisions/D06.md) — blocker semantics: [!] + reason, continue independent items, never ask user
- [D07](decisions/D07.md) — multi-architect review is file-threaded: one file per reviewer, S-ids, 2-round cap
- [D08](decisions/D08.md) — sub-plan threshold: split only when >150 lines or phases independently executable
- [D09](decisions/D09.md) — standard executor kickoff prompt lives in flows/PLAN.md, routes to EXEC.md
- [D10](decisions/D10.md) — pre-split monolithic core superseded by flows/ + this log; backup kept untracked
- [D11](decisions/D11.md) — numbered Design-Q/Decision sub-structure in PLAN.md for 2+ open choices
- [D12](decisions/D12.md) — external-behavior verification: decisions on undocumented 3rd-party behavior checked vs real system
- [D13](decisions/D13.md) — dependency-maturity check: new deps need registry metadata before recommending
- [D14](decisions/D14.md) — per-item go-ahead: bundled approvals record consent per item
- [D15](decisions/D15.md) — verification must be environment-complete: setup counts as a command
- [D16](decisions/D16.md) — any executor-facing rule must be reachable from the kickoff prompt, not just core routing
- [D17](decisions/D17.md) — local-AGENTS read rule: read both .agents/AGENTS.md and root AGENTS.md
- [D18](decisions/D18.md) — nested/scoped AGENTS.md: read root→subtree, deeper extends/overrides broader
- [D19](decisions/D19.md) — ownership flip: agents write dir-level AGENTS.md; .agents/AGENTS.md is user-managed, wins on conflict
- [D20](decisions/D20.md) — global plans dir is scratch-only; plans always live under project .agents/plans/
- [D21](decisions/D21.md) — post-recovery merges: skip≠pass, benchmark baselines, no downgrading required items
- [D22](decisions/D22.md) — no private-plan leakage in committed artifacts: describe behavior, not provenance
- [D23](decisions/D23.md) — project AGENTS.md mirrors the global core+routing split
- [D24](decisions/D24.md) — config source of truth is this repo; payload isolated under payload/, installs 1:1
- [D25](decisions/D25.md) — global-config misses captured privately by default in ~/.agents/dotagents/findings/
- [D26](decisions/D26.md) — executors may leave Status: draft follow-up plans for out-of-scope discoveries
- [D27](decisions/D27.md) — plan indexing left OPEN: no index/dependency convention chosen yet
- [D28](decisions/D28.md) — ~/.agents/dotagents/ is the private working area; triage folds findings into this log
- [D29](decisions/D29.md) — release-collateral standard: bench JSON history + RELEASENOTES.md for perf repos
- [D30](decisions/D30.md) — live progress-marking primed at kickoff + gated at handoff
- [D31](decisions/D31.md) — token discipline as an always-on core rule: wrap logs, never read whole to learn one bit
- [D32](decisions/D32.md) — executor model gated at ready: Executor: line required in every plan
- [D33](decisions/D33.md) — installable dotagents CLI package (init/install/build-pyz/audit), built on duho + pathlib_next
- [D33-log-split](decisions/D33-log-split.md) — design log split onto the memory pattern: DECISIONS.md index + one file per decision
- [D34](decisions/D34.md) — Python version policy (run latest, test floor) + venv naming .venv/<ver>-<os>-<arch> + Python Manager, avoid Store Python
- [D35](decisions/D35.md) — payload/ decomposed into base overlay + opt-in overlays/<name>/; required tooling at top-level tools/ (supersedes D24)
- [D36](decisions/D36.md) — installer is overlay-agnostic: init=base, install=base + copy-only --overlays <path>; overlay mgmt deferred to a future subcommand
- [D37](decisions/D37.md) — private-agents git sync: ~/.agents is one private repo, per-project .agents symlinks to projects/<name>; dotagents link/sync + private-sync overlay
- [D38](decisions/D38.md) — migrate CLI to duho >= 0.3.3 (Args/Cmd split, commands are (LoggingArgs, Cmd) with __call__); shim duho's zipapp AST-introspection bug so the .pyz keeps flags/help
- [D39](decisions/D39.md) — cloud-setup self-heals the container-start clone (retry/backoff + persisted SessionStart recovery hook) so one early egress-race failure can't permanently disable the environment
- [D40](decisions/D40.md) — dotagents sync authenticates the private repo itself (token + github→proxy-rewrite bypass), so a standalone CLI sync works without the private-sync Stop hook
- [D41](decisions/D41.md) — leak_check scans commit messages for agent-session trailers/URLs (Claude-Session:, claude.ai/code/session), not just tracked files, so a harness-added session id can't reach public history unnoticed
- [D42](decisions/D42.md) — cloud-setup wires the recovery hook on the no-remote-at-setup branch too (not just clone failure), so a secret absent from the setup-script phase but present in-session heals next session
- [D43](decisions/D43.md) — link/sync never adopt or copy back a `<project>/.agents` that is itself a git checkout (e.g. a hosted-runner session-source clone), so the store can't swallow a foreign repo; `--force` backs it up and links the store
- [D44](decisions/D44.md) — plans use named role/subrole executor routing with host-native providers; raw model IDs stay in MODELS.md
- [D45](decisions/D45.md) — the calling agent chooses the host-native provider lane; cross-provider fallbacks require explicit permission
- [D46](decisions/D46.md) — OpenAI role defaults use Luna for scoped implementation, Terra for broad exploration, and Sol for high-blast-radius work
- [D47](decisions/D47.md) — root AGENTS.md is the committed public library-interface doc (public API/args/gotchas, ships in the package); private working notes move under .agents/; .gitignore no longer excludes AGENTS.md
- [D48](decisions/D48.md) — the "never print DOTAGENTS_* values" guard is an always-on Leakage rule, not on-demand kb content: the leak happens during first-look triage, before any routing line fires
- [D49](decisions/D49.md) — token budgets are WARN-only and were silently over; record real sizes (core AGENTS.md 3649B/2500B, REPO.md 7012B/3000B) and treat each WARN as recorded debt
- [D50](decisions/D50.md) — config-internal cross-refs are absolute (`~/.agents/` prefix): the file is inlined by Claude and symlinked by Codex/Antigravity, so a bare relative path resolves three different wrong ways
- [D51](decisions/D51.md) — repo-mutating git commands are strictly sequential (one index lock); plan phases parallelize by write-scope matrix. The discriminator is shared mutable state, not logical independence
- [D52](decisions/D52.md) — repo standard ships three workflows: docs get a dispatchable docs.yml so Pages is exercised before the first release; enabling Pages is two owner-only API calls, not one
- [D53](decisions/D53.md) — leak_check hard-fails only on multi-word plan basenames; generic one-word names WARN, since they collide with legitimate public doc filenames
