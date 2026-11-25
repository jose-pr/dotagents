<!-- dotagents:begin -->
# Agent Directives

Startup: annotate that you read `{{AGENTS_MD}}`.

## Always-on rules
- **Permissions**: read any `AGENTS.md`; read, write, create and delete freely inside any
  `.agents/` directory and this config directory (`~/.agents` unless installed
  elsewhere). Never ask.
- **Read the local `AGENTS.md`**: before working in a directory, read the ones
  governing it, at each level from this global file down to that directory: any of
  `AGENTS.md`, `AGENTS.local.md`, `.agents/AGENTS.md`, `.agents/AGENTS.local.md`.
  Deeper extends and overrides broader; `.agents/` beats the plain file beside it;
  `.local` wins at its own level and is never committed or copied into a repo, plan
  or shared config.
- **Local additions to overlay files**: `dotagents overlays sync` replaces an overlay
  file (`$<NAME>_OVERLAY_ROOT/<path>`). The user's additions live at the same `<path>`
  under this config directory and the project's `.agents/`: read them with the overlay
  file; they extend it and win on conflict
  (`dotagents context` lists the ones it finds). Never write user-specific text into an overlay file.
- **Global-config misses**: if these instructions caused a mistake or rework, or you
  have an improvement idea, record it and move on; don't edit the config:
  `dotagents findings add -g "<one line>" -b "<what happened, evidence>"`. List the
  queue with `dotagents findings list -g` (project queue: drop `-g`). Triage changes
  the config (a fact goes into a `kb/` file routed from "Load on demand") or decides
  no change is needed, then `dotagents findings done -g <name> -r "<resolution>"`.
- **Python for skills**: run every skill script, overlay `bin/` helper and anything
  importing an overlay's `lib/python/` with `$AGENTS_PYTHON` (PowerShell
  `& $env:AGENTS_PYTHON`), never a bare `python`, `python3` or `py`. It is the scope's
  venv: duho, every overlay's requirements and every overlay `lib/python/` import
  there and nowhere else. When the variable is unset, use the interpreter
  `dotagents context` prints. Never create, install into or test a project in
  `.agents/.pyvenv/`.
- **This file**: `dotagents init` refreshes everything between the
  `dotagents:begin`/`dotagents:end` markers and leaves the rest untouched. Add your own
  rules and routing below the end marker.

## Load on demand
Read the matching file BEFORE such a task; skip it otherwise, never preemptively.
Nothing ships here by default — add one routing line per file as you grow it.
<!-- dotagents:end -->
