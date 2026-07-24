# dotagents — package API header

Public API of the installed `dotagents` package: the CLI umbrella plus the `_*.py`
helper modules that back it. This file ships inside the package so a consuming agent
can read it without the source. Full docs: https://jose-pr.github.io/dotagents/

## Entry points

- `dotagents.cli.main(argv=None) -> int` — the `install.py` shim and
  `python -m dotagents` entry point. Repoints zipapp sources, then dispatches through
  `duho.app` with the discovered command set. Also the `dotagents` console script
  (`[project.scripts]`).
- `dotagents.cli.Dotagents(LoggingArgs, Cli)` — the umbrella CLI class.
- Compiled command classes live in `dotagents.cli.<name>` (`init`, `overlays`,
  `context`, `env`, `build_pyz`); each is a `class X(LoggingArgs, Cmd)` with a
  `__call__`. That is the WHOLE shipped surface — dotagents bundles no command
  module of its own. `link` / `sync` left the package with their logic (D85): the
  opt-in **private-sync** overlay ships them, renamed `link-project` /
  `sync-project`, from its own `cmds/` + `lib/_link.py`. `leak-check` is likewise
  not in the repo — a personal command module the user keeps in their private
  `<scope>/dotagents/cmds/` (D84). `audit` is repo CI tooling (`tools/audit.py`),
  not a command.
- Command discovery layers sources, later wins: built-ins < bundled `cmds` <
  overlay `cmds` (`<overlay-root>/cmds`) < scope `cmds` dirs (user + project) <
  `$AGENTS_CMDS_PATH` < `--cmdspath`. The overlay + scope tiers come from one
  Contract-A `get_file_paths` walk (`cli._cmds_dirs`), the same resolver that
  backs `bin`/PATH.

## Helper modules (public surface)

- `_agents` — `Agent` base type + per-agent adapters; `stamp_identity(...)` emits the
  standardized `AGENTS_*` / `AGENT` identity vars.
- `_overlays` — `install_overlay` / `read_manifest` / `find_setup_script` /
  `run_setup_script`; installs an overlay's files and collects its `routing` / `rules`
  contributions to the managed `AGENTS.md` block. `DEFAULT_PRIORITY = 500`.
- `_scope` — `resolve_scope(global_scope, agents_dir=None)` and `resolve_source(...)`;
  scope = *where installed overlays live* (user = the configurable store, project =
  `<project>/.agents`), source = *where an overlay comes from* (bundled by default).
  Installed overlays are **discovered** by presence, not tracked in a registry.
- `_context` — assemble the effective per-agent context (Plan 04); reads overlay
  `priority` from the manifest (lower sorts earlier).
- `_env` — chained env-file assembly + `env.py` execution (frozen contract B):
  `get_environment` / `get_diff` / `resolve_env_files` / `get_env_from_py` /
  `get_env_from_file`. Bins onto PATH first, then two tiers (`pre.env*` then `env*`),
  later-overrides-earlier. Identity seeded before the chain; proxy vars applied after.
- `_resolve` — `get_file_paths(*names, agents_dir, project_root, global_scope=False,
  include_missing=False)`: the Contract-A precedence walk / filename resolution.
- `_merge` — managed-block merge for `init`'s `AGENTS.md` / `CLAUDE.md`, delimited by
  `<!-- dotagents:begin -->` / `<!-- dotagents:end -->`. Detection is by marker
  presence only, so it survives user reformatting. `begin_marker`/`end_marker`
  override the pair for other comment syntaxes (`#` for TOML), and `append=True`
  puts a first-time block at the END of the file — required for TOML, where a
  `[table]` header captures every key line after it and a prepended block would
  swallow the user's top-level keys.
- `_skills` — publish an overlay's `skills/<name>/` into a scope's shared skills dir
  (symlink-preferred, copy fallback); unpublish removes only what the overlay
  published, then sweeps broken symlinks. Pure stdlib.
- `_hooks` — additive, idempotent merge of our hooks into an agent's `settings.json`.
  `hooks.<Event>` is a **list of matcher-objects** each holding its own `hooks` list,
  not a flat command list. Foreign hooks are preserved verbatim, malformed entries
  are dropped rather than raising, and invalid JSON raises `SystemExit` instead of
  silently overwriting the user's file. `shell` (Claude: `"bash"`/`"powershell"`,
  picks the interpreter for the hook's own command) and `command_windows`
  (Codex: emitted as `commandWindows`, a separate Windows-only command OVERRIDE,
  not an interpreter choice) are both supported. Pure stdlib. Consumed by
  `ClaudeAgent.wire_hooks` (`~/.claude/settings.json`: env via `$CLAUDE_ENV_FILE`
  + context via stdout, plus `CwdChanged`, plus a `PreToolUse` env-loader for the
  PowerShell tool) and `CodexAgent.wire_hooks` (`<CODEX_HOME|~/.codex>/hooks.json`,
  never `config.toml`: `SessionStart` context-only, plus a `PreToolUse` env-loader
  matched on `matcher: "Bash"`). Codex's hook JSON is structurally identical to
  Claude's, including the SAME `updatedInput.command` rewrite mechanism on
  `PreToolUse` — confirmed directly against Codex's own docs
  (learn.chatgpt.com/docs/hooks), not assumed from Claude parity.
  Gemini/Cursor/Copilot keep the base no-op — Gemini CLI proper has no hook
  mechanism documented at all (checked directly). **Antigravity is a separate
  product** from Gemini CLI (Antigravity CLI/IDE/SDK family, shares only the
  `~/.gemini/` namespace for some files) and DOES wire a hook: see
  `AntigravityAgent.wire_hooks` below. `PreToolUse` there is still allow/deny/ask
  only — no `updatedInput`, so no command-rewrite/env-loader path exists to hang
  on it, unlike Claude/Codex. Revisit both conclusions if either framework changes.
- **`AntigravityAgent.wire_hooks`** (`<AGENTS_HOME_ANTIGRAVITY|~/.gemini/config>/hooks.json`,
  keyed under a `"dotagents"` name per the docs' own example shape — a named-entry
  object, not Claude/Codex's flat `hooks.<Event>`): wires a single `PreInvocation`
  entry, context-only, no env mechanism. Antigravity's hooks
  (antigravity.google/docs/hooks) have exactly five events — `PreToolUse`,
  `PostToolUse`, `PreInvocation`, `PostInvocation`, `Stop` — no SessionStart
  equivalent. `PreInvocation` fires every model turn (`invocationNum`, 0-indexed),
  so the deployed script (`preinvocation_antigravity_context.py`,
  `_overlay/dotagents/hooks/`) gates on `invocationNum == 0` to behave like a
  one-shot SessionStart rather than resending context every turn. Output shape is
  a bare `{"injectSteps": [{"ephemeralMessage": "..."}]}`, no `hookSpecificOutput`
  wrapper — confirmed against the primary docs after an earlier pass here wrongly
  concluded no useful injection was possible; `ephemeralMessage` is the one of the
  three step types meant for free text (`toolCall` executes a tool,
  `userMessage` impersonates the user). No detection marker exists anywhere in
  Antigravity's docs, so `detect_env_vars = []` — explicit `--agents antigravity`
  only, same posture as Codex's env-block precedent (writes touching an agent's
  own live config are opt-in, never inferred).
- **`SessionStart`/`CwdChanged` register TWO handlers each**, bash-syntax
  (default shell) and a PowerShell-native equivalent (`shell: "powershell"`).
  hooks.md: `shell` "Defaults to bash, or to powershell on Windows when Git Bash
  isn't installed" — verified directly that bash syntax fed to `powershell
  -Command` on such a machine is a hard parse error, not a soft failure, so
  every session there would silently get neither env nor context. Every handler
  in a matched group fires unconditionally (hooks.md), so both always run; the
  one whose interpreter is absent fails harmlessly. The PowerShell
  `SessionStart` variant is context-only (`dotagents context`), not
  env+context: `$CLAUDE_ENV_FILE`'s documented effect is "subsequent BASH
  commands" regardless of which shell wrote it, so writing to it from a
  PowerShell-shelled hook would feed nothing.
- **Windows only**: `ClaudeAgent._wire_powershell_pretooluse` additionally wires
  a no-matcher `PreToolUse` hook (fires on every tool call), `shell:
  "powershell"`, running `PRETOOLUSE_POWERSHELL_COMMAND` INLINE — deliberately
  not a `.ps1` file, since a script file is subject to PowerShell's execution
  policy (RemoteSigned/AllSigned/Restricted) and dotagents has no code-signing
  certificate; verified directly that the inline form runs successfully even
  under `Restricted`, which blocks every `.ps1` file outright. Closes a real
  gap: `$CLAUDE_ENV_FILE` is Bash-tool-only (confirmed empirically that
  `$env:CLAUDE_ENV_FILE` is empty inside a live PowerShell tool call), so the
  SessionStart env half never reaches the PowerShell tool. Uses `PreToolUse`'s
  `updatedInput` to prepend a guarded env-loader (`AGENTS_RUNTIME_SET`,
  matching the precursor's convention) to a `PowerShell` tool call's own
  command — not by trying to persist state across hook invocations, which are
  each their own fresh process and cannot. Whether the guard is visible across
  separate PowerShell tool calls is UNVERIFIED. Every literal `\` in the
  command constant must be a raw string — a bare `\b` in a normal Python string
  literal silently becomes a backspace character, corrupting the emitted path;
  caught once by testing a draft through a real PowerShell spawn.
- **`CodexAgent._deploy_pretooluse_script`** covers the same env gap for Codex,
  which has NO env-persistence mechanism at any hook event (not Bash-only like
  Claude — none). Ships `pretooluse_codex_env.py` (`_overlay/dotagents/hooks/`),
  deployed to `<codex-home>/hooks/` (create-or-refresh), wired as `PreToolUse`
  with `matcher: "Bash"` — Codex's one shell tool, so (unlike Claude's
  no-matcher hook) filtering happens at the settings level, no runtime
  `tool_name` check needed in the script. A FILE, not inlined like Claude's:
  Codex's docs show every hook example as `python3 <path>`, and a `.py` file
  has no execution-policy/signing concern (PowerShell-specific). Sets
  `commandWindows` to `python "<path>"` (not `python3`) — verified directly
  that `python3` resolves to the Microsoft Store app-execution-alias stub and
  fails outright on this dev machine (exit 49), the same trap noted elsewhere
  for `py`/venv creation.
- `_sync` — `PathSyncer` wrapper reproducing `install`'s backup/copy/report; requires
  `pathlib_next.Path` instances (not plain `pathlib.Path`) and a pre-created parent dir.

## Environment variables

The prefix split (D80): **`AGENTS_*`** names everything about the `.agents` / agent
world (paths, scope, overlays, sync) — non-secret, safe to emit; **`DOTAGENTS_*`** is
reserved for genuinely tool-internal config and secrets, so the "never print
`DOTAGENTS_*` values" leak guard (D48) stays a simple blanket ban over exactly the
sensitive set.

Config / path / sync vars (`AGENTS_*`, non-secret — read, and some emitted):

- `AGENTS_HOME` — the configurable user-scope store path (default `~/.agents`). Also
  **emitted** by `dotagents env` (D79) and set for overlay setup scripts / sync hooks.
- `AGENTS_STORE_DIR` — per-project store location (absolute paths allowed).
- `AGENTS_OVERLAYS_SRC` — default overlay source dir for `overlays`.
- `AGENTS_CMDS_PATH` — extra command-module search paths (os.pathsep-split).
- `AGENTS_OVERLAY_DIR` — set for an overlay's setup script (its own installed dir).
- `AGENTS_REMOTE` / `AGENTS_SYNC_MESSAGE` — private-store sync (tokenless remote URL /
  commit message).

Every reader above prefers the `AGENTS_*` name and falls back to the old
`DOTAGENTS_*` name (`DOTAGENTS_AGENTS_DIR`, `DOTAGENTS_STORE_DIR`,
`DOTAGENTS_OVERLAYS_SRC`, `DOTAGENTS_CMDS_PATH`, `DOTAGENTS_OVERLAY_DIR`,
`DOTAGENTS_AGENTS_REMOTE`, `DOTAGENTS_SYNC_MESSAGE`) for one release — deprecated,
removable next. Setters emit both names this release.

Tool-internal / secret vars (`DOTAGENTS_*` — kept; read, **never printed**):

- `DOTAGENTS_AGENTS_TOKEN` — **secret** (fine-grained PAT) for private-store auth.
- `DOTAGENTS_CLI_INSTALL` — pip spec to install the CLI itself (tool-specific).
- `DOTAGENTS_AUDIT_PATTERNS` — path to the machine-local audit-pattern file (tooling).

Emitted by the identity/env layer (safe to branch on in env files):
`AGENTS_HARNESS`, `AGENTS_VENDOR`, `AGENTS_MODEL`, `AGENTS_AGENT` / `AGENT`,
`AGENTS_CODE_SESSION_ID`, `AGENTS_PROXY`, `AGENTS_WEBFETCH_PROXY_URL`, plus the two
scope roots — `AGENTS_HOME` (the user store, `agents_dir`/`~/.agents`) and
`AGENTS_PROJECT_ROOT` (this project's root). Both are seeded only if unset, so a
harness/env can pin them. `resolve_scope` READS `AGENTS_PROJECT_ROOT` (then the
agent-native `CLAUDE_PROJECT_DIR`, then cwd) for the project scope's root.

## Gotchas

- **Python 3.9 floor.** Files using bare `X | Y` unions in runtime-evaluated positions
  need `from __future__ import annotations`. `Path.write_text(..., newline=...)` is
  3.10+, so wrapper-script writers use `open(path, "w", newline="")`.
- **Zipapp source shim.** Inside a `.pyz`, `Path(__file__)` is not a real file, so
  duho's AST flag/help introspection degrades (`--from` → `--from-`, positionals lost).
  `cli.main()` calls `_repoint_zipapp_sources()` first, extracting the built-in command
  modules to real temp files. Discovered `cmds` modules are extracted by
  `_package_data_dir` before import, so they need no repoint.
- **Package data in a `.pyz`.** `_package_data_dir()` resolves `skeleton/` and the
  bundled payload via `importlib.resources` (a zip-backed `Traversable` is extracted to
  a temp dir once), never `Path(__file__).exists()` (always False in a zipapp).
- **`pathlib_next` needs `typing_extensions` on Python < 3.10** (an upstream gap); a
  3.9 environment must `pip install typing_extensions`.
