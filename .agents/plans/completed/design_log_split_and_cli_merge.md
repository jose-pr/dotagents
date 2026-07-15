# Design-log split (memory pattern) + CLI-package merge

Status: done
Executor: (self — main thread holds full context; no cold subagent) — this is a
mechanical-but-judgment reorg: splitting entries, reconciling a numbering collision,
updating cross-refs. Sonnet-class judgment, already in context.

Merge the completed dotagents-CLI worktree into main, and reorganize the monolithic
design log into the ecosystem's **memory pattern** (lean index + one file per entry),
cross-agent compatible (plain `.agents/` paths, no harness-specific location).

## Progress
- [x] Phase A — Bring non-conflicting CLI package files into main (src/, pyproject,
      install.py shim, CI, .gitignore, README, CHANGELOG)
- [x] Phase B — Split repo design log onto memory pattern + reconcile D-collision
- [x] Phase C — base overlay (renamed skeleton→_overlay) uses the pattern; DECISIONS.md
      + decisions/.gitkeep; cli.py/AGENTS.md/README updated
- [x] Phase D — Reconcile AGENTS.md overlap + update all references to new paths
- [x] Phase E — Re-audit (3x) + reinstall + verify CLI init/install + report

## Known Facts & Context

**Memory pattern being mirrored** (from this ecosystem's `MEMORY.md` + per-fact files):
one index file, one file per entry, index loaded each relevant session, entries opened
on demand. Cross-agent: lives under `.agents/`, plain-path links, no `~/.claude/`.

**Target layout** (replaces monolith `.agents/dotagents.md`). Mirrors the memory
pattern's `memory/` dir + `MEMORY.md` sibling index — **index filename matches the dir
it indexes**:
```
.agents/dotagents/
  DECISIONS.md           index: header (goals/cost-model, how-to-iterate, public-
                         sanitize rule) + one scannable line per decision. Sibling to
                         and same stem as decisions/ (like MEMORY.md ↔ memory/).
  decisions/D01.md..     one file per decision; each self-contained: title, date,
                         finding(s) that prompted it, the decision, provenance,
                         superseded-by. Findings fold INTO their decision (F↔D is
                         many-to-one; no separate F-files).
  NOTES.md               the non-D/F prose: review-protocol, size-mapping table,
                         byte-tables, template-regen note. (Sibling index for the
                         notes; no dir needed — it's short, one file.)
```
Naming rule (user directive): a directory and its index file share a stem —
`decisions/` ↔ `DECISIONS.md`. Never `decisions/` ↔ `log.md`.

**Source content**: the full monolith is `.agents/dotagents.md` (397 lines, in tree).
Decisions D1–D28 + my reconciled D29–D32 are there. Findings F1–F21 map into those
decisions. Non-decision sections: Goals, Multi-architect review protocol, Ideas
backlog, How to iterate, Size-discipline mapping, Byte tables, Template regeneration.

**D-collision**: the worktree also appended CLI decisions to the OLD file path
(`.agents/plans/config_design_log.md`), numbering its first entry **D29** — colliding
with my reconciled D29–D32. Resolution (user: "you can cleanup / reorganize"): my
reconciled entries keep D29–D32; the worktree's CLI decisions become **D33** (the CLI
package) with the D-init/source/vendor/wrapper/merge sub-decisions folded into D33's
file as the design record (they're all facets of the one CLI-package decision, already
captured in `.agents/plans/dotagents_cli_package.md`). The worktree's edit to the old
file path is NOT carried over as a file — only its content, into D33.

**Worktree location**: `.claude/worktrees/agent-a8be6941dda4bcc1f`. Its modified
overlap files: `AGENTS.md` (appended arch-notes — carry over), `install.py` (shim —
already copied in Phase A), `.agents/plans/config_design_log.md` (content → D33).

**CLI gotchas to preserve** (worktree recorded them; land in repo-root `AGENTS.md`
arch-notes, already appended in worktree AGENTS.md — carry that section over):
pathlib_next needs typing_extensions on <3.10; `.pyz` `Path(__file__)` not real →
importlib.resources; `write_text(newline=)` needs 3.10+; PathSyncer needs
pathlib_next.Path both sides + no auto-mkdir parent.

**Audit facts**: `--check-templates` needs `py -3.12` (3.11+). Hygiene allowlist
already fixed in main (pathlib_next/duho/yaconfiglib/pydhcp removed from
PERSONAL_PATTERNS). All three audits were PASS before this reorg.

## Phases

### Phase B — split repo design log
Create `.agents/dotagents/DECISIONS.md` (index) + `.agents/dotagents/decisions/
D01.md … D33.md` + `.agents/dotagents/NOTES.md`. Move each decision's prose into its
file, folding the finding(s) it resolves. Index line shape: `- **D07** · multi-
architect review is file-threaded · [decisions/D07.md](decisions/D07.md)`. Keep index
header: Goals, cost-model line, how-to-iterate steps, public-sanitize rule. `git rm`
`.agents/dotagents.md` after. Reconcile D-collision per Known Facts (my D29–D32,
worktree → D33). Done when: `.agents/dotagents.md` gone; index lists D1–D33; each Dnn
file exists and is self-contained; no duplicate D-number.

### Phase C — skeleton uses the same pattern
Rename skeleton `src/dotagents/skeleton/dotagents/log.md` →
`src/dotagents/skeleton/dotagents/DECISIONS.md` (stem matches its future `decisions/`
dir), describe the index+per-file convention in it, and ship an empty
`dotagents/decisions/.gitkeep`, so `dotagents init` teaches the pattern. Update
skeleton `README.md` layout section (mention `dotagents/DECISIONS.md` + `decisions/`)
and `_merge.py`/`cli.py`/skeleton `AGENTS.md` if they name `dotagents/log.md`. Keep it
minimal — skeleton has zero real decisions. Done when: skeleton `DECISIONS.md`
documents the convention; `decisions/.gitkeep` present; no skeleton ref to `log.md`.

### Phase D — reconcile AGENTS.md + references
Repo-root `AGENTS.md`: keep my design-log-path edits, append the worktree's
"dotagents CLI package — architecture notes" section, and point every design-log
reference at `.agents/dotagents/DECISIONS.md` (the index). Update `payload/kb/RECOVERY.md`
(already generic — verify), and both plans (`dotagents_cli_package.md`,
this plan) to the new index path. Grep for any lingering `config_design_log` or
`.agents/dotagents.md` (non-historical). Done when: no stale references; AGENTS.md
carries the arch-notes.

### Phase E — audit + reinstall + report
Run the three audits (py -3.12 for templates), reinstall payload, report. Done when:
all three PASS; install.py (now the CLI shim) still installs the payload; changed-file
list reported.

## Verification
```
cd <repo-root>
ls .agents/dotagents/decisions/ | wc -l              # expect 33
test ! -f .agents/dotagents.md && echo "monolith removed"
grep -rl 'config_design_log\|agents/dotagents\.md' AGENTS.md payload .agents/plans/*.md  # expect empty (only historical in decision files ok)
py -3.12 payload/tools/audit_config.py --root payload
py -3.12 payload/tools/audit_config.py --check-templates --root payload
py -3.12 payload/tools/audit_config.py --repo-hygiene .
python install.py install --dest /tmp/da-verify --from payload   # CLI shim still installs
```
Expected: 33 decision files, monolith gone, no stale refs, all audits PASS, install
reproduces payload.

## Reporting
Live-update Progress. This reorg's own decision (the split) is recorded as a new
decision file in the new scheme. Nothing committed until the user asks.
