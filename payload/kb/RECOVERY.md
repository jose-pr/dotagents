# Recovering Lost Agent Config / Files

Read this when `~/.agents` (or any agent-authored file) is lost or corrupted and needs
reconstruction. Distilled from a real loss+recovery of `~/.agents` (recorded in the
config's design log).

## Priority order of sources (best first)

1. **A live session that authored the files.** If any agent session that wrote the
   files is still open, ask it first — it holds verbatim content in context and can
   restore byte-exact. Everything else below is for when no such session exists.
2. **Exact content-hash snapshots** (byte-exact, not paraphrases):
   - VS Code Copilot/Edit chat-editing sessions:
     `%APPDATA%\Code\User\workspaceStorage\<hash>\chatEditingSessions\<id>\contents\`
     — files Copilot touched are stored whole under content-hash names; map them via
     the session's `state.json`.
   - VS Code local history: `%APPDATA%\Code\User\History\` — timestamped snapshots of
     files opened/edited in VS Code, including files outside any workspace.
   - Antigravity IDE conversation DBs:
     `~/.gemini/antigravity-ide/conversations/*.sqlite` — tool steps embed the full
     content of every file the agent *read or wrote*; also
     `~/.gemini/antigravity-ide/brain/` and
     `%APPDATA%\Antigravity IDE\User\History\`.
3. **Agent transcripts** (verbatim inside tool calls, needs extraction):
   - Claude Code: `~/.claude/projects/<project-slug>/*.jsonl` — every Read result and
     Write/Edit payload is in the transcript; the full file content of anything the
     session read or wrote can be replayed from it.
   - Claude Code file history: `~/.claude/file-history/` (and, when `~/.agents` hosted
     other harnesses, their equivalents) — pre-edit versions of edited files.
   - Codex/other CLI session logs under their own state dirs.
4. **Derived copies in repos**: files generated *from* the lost ones (real
   `.github/workflows/` generated from templates, project plans following the flow
   formats) — the newest standardized repo is the best exemplar
   (your most recently standardized repos; newest file wins).
5. **Model memory / paraphrase rebuild** — last resort; produces plausible but lossy
   text. Label such files as rebuilt and keep them separate from exact snapshots.

## Process

1. **Stop writes** to the affected tree; inventory what survives (`find`, mtimes).
2. Collect evidence into a quarantine dir (e.g. `<tree>/recovered/`) — copy snapshots
   **as-is**, never "fix" them; keep a `manifest.json` per file: source path, source
   timestamp, confidence (exact-snapshot / transcript / rebuilt), byte size.
3. Prefer exact snapshots; use rebuilds only to fill gaps, and **diff rebuilds against
   exact sources** — rebuilds silently drop rationale and invent plausible details.
4. **Verify, don't trust**: byte-compare restored files against exact snapshots where
   they exist; for deterministically-transformed files, re-apply the transform to the
   exact original and compare.
5. Record provenance afterward (in the config's design log): what was restored from
   where, what remains lossy, what should be regenerated later.
6. Only delete the quarantine dir once everything worth keeping is merged and the
   provenance note exists.
7. After restoring, run `py -3.12 ~/.agents/tools/audit_config.py` — it checks the
   full config manifest, forbidden patterns, and prints the size table.

## Gotchas

- **Symlink traversal is the #1 destroyer**: a repo may contain a symlink/junction into
  the real config (e.g. `<repo>/.agents/global -> ~/.agents`). A recursive
  delete or "cleanup" that follows it wipes the target, not a copy. Before ANY
  recursive delete under a `.agents/` tree: `ls -la` and check for `l`/LinkType
  entries; delete links with `rm <link>` (no trailing slash, never `-r` through them).

- A live session's in-context config can diverge from disk (it predates the loss) —
  that's an asset for recovery, but tell the session to reload before it *writes*
  based on stale assumptions about the tree.
- Transcript/DB extraction beats memory even for the authoring session's older turns.
- Path-only evidence (directory listings, links) proves a file existed — never turn it
  into a fake file; list it as lost instead.
- Harness "modified since read" guards fire a lot during restoration; re-read then
  write, don't force.
