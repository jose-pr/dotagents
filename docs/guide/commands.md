# Commands

The `dotagents` CLI is an umbrella of subcommands. Run it as the installed
`dotagents` wrapper, as `python -m dotagents`, via the `python install.py <cmd>`
dev shim (from a source checkout), or from a built `dotagents.pyz`. Most commands
take a scope flag: **project**
by default (the `<cwd>/.agents` store, when run inside a project) or **user** with
`-g` / `--global` (the `~/.agents` store, configurable).

## Command set

| Command | What it does |
| --- | --- |
| `init` | Lay down the neutral base config; block-merge `AGENTS.md`/`CLAUDE.md`; `--bin-dir` also writes a PATH wrapper. |
| `overlays` | Manage opt-in overlays by name: `add` / `remove` / `list` / `sync` / `show`. |
| `context` | Assemble the effective context for one or more agents. |
| `env` | Assemble the chained env-file layers + identity vars, in a chosen format. |
| `build-pyz` | Build the self-contained `dotagents.pyz` zipapp. |
| `findings` | Per-scope findings queue: `add` / `list` / `show` / `done` / `reopen` / `remove` / `index` / `path`. |
| `launch` | Start an agent's CLI with the `env` exported and the `context` handed over; everything after `--` goes to the agent. |

That table is the **whole** shipped surface; `findings` and `launch` are the two bundled
command modules (discovered from the package itself, overridable by a same-named module
in a scope's `cmds/`). Everything else is **discovered**, from each **installed overlay's** `cmds/`
dir, each scope's command dir, `$AGENTS_CMDS_PATH` entries, and `--cmdspath` — one
Contract-A resolver walk covers the overlay + scope tiers (D84). Two consequences
worth knowing:

- `link-project` / `sync-project` (the per-project private-store workflow) come from
  the opt-in **`private-sync` overlay**, which ships the commands and their logic
  together — install it and they appear (see [link-project / sync-project](#link-project-sync-project)).
- A personal command module dropped into a scope's `dotagents/cmds/` is discovered
  like any other, so tooling you keep private never has to live in this repo.

Your own commands work the same way: drop a `*.py` defining a `duho` command class
into `<scope>/dotagents/cmds/` (`init` creates that dir) and it becomes a subcommand,
no registration needed.

## init

See [Install](install.md) for the full walkthrough. In brief:

```bash
dotagents init                          # base config into <cwd>/.agents (project scope)
dotagents init -g                       # ...into ~/.agents (user scope)
dotagents init --bin-dir ~/.local/bin   # also write a `dotagents` command on PATH
dotagents init --no-hooks               # skip agent hook wiring + the skills link
dotagents init --dry-run
```

### Hook wiring and the skills link

For each active agent that supports it (today: Claude), `init` also merges two hooks
into the agent's `settings.json` and links the scope's shared `skills/` dir into the
agent's config dir. Pass `--no-hooks` to skip both.

| Hook | Command | Why |
| --- | --- | --- |
| `SessionStart` | appends `dotagents env --diff --format export` to `$CLAUDE_ENV_FILE`, then runs `dotagents context` | Claude sources `$CLAUDE_ENV_FILE` before each Bash command, so the env layers reach every command in the session; and it injects the hook's **stdout into the session context**, which is how the assembled context reaches the model. |
| `CwdChanged` | `[ -f AGENTS.md ] && cat AGENTS.md \|\| true` | Surfaces a directory's `AGENTS.md` when the agent changes into it. |

The env half **appends** (`>>`) and is guarded by `[ -n "$CLAUDE_ENV_FILE" ]`, both
per the [hooks docs](https://code.claude.com/docs/en/hooks#persist-environment-variables):
other hooks write to the same file, so `>` would discard their variables, and an
unguarded redirect would create a file literally named `""` where the variable is
unset. `$CLAUDE_ENV_FILE` exists only inside SessionStart/Setup/CwdChanged/FileChanged
hook processes — it is absent from the session's own shell, so checking for it with
`env | grep` proves nothing.

The merge is additive and idempotent: unrelated settings keys and hooks you wrote
yourself are preserved verbatim, and re-running `init` writes nothing.

The skills link is what makes published skills visible — publishing into
`<scope>/skills/` only helps if the agent reads that directory. It is a symlink where
the OS permits one and a **copy** otherwise (notably Windows without Developer Mode).
A copy is a point-in-time snapshot: re-run `dotagents init` to refresh it after
overlay skills change.

### Codex

Codex ships a [hooks framework](https://learn.chatgpt.com/docs/hooks) whose JSON is
structurally identical to Claude's, so the same merge applies. `init` writes a
`SessionStart` hook running `dotagents context` into `~/.codex/hooks.json` (or
`$CODEX_HOME/hooks.json`) — Codex adds a SessionStart hook's plain stdout as extra
developer context.

We target `hooks.json` rather than `config.toml` so your main config is never rewritten
for hooks — if you keep inline `[hooks]` in `config.toml`, Codex warns about the split,
so use `--no-hooks` and add the hook there yourself.

**Env is a static snapshot, not a hook**, and it is written **only when you name the
agent explicitly**:

```bash
dotagents init --agents codex          # writes the env block
dotagents init                         # never writes it, even if Codex is detected
```

This edits your main `config.toml` with values that go stale, so it has to be asked
for — being detected is not consent.

Codex has no `$CLAUDE_ENV_FILE` equivalent, does not read `.env` files, and has no
event that fires before config load (hooks are defined *in* the config, and its
earliest event is `SessionStart`). The only mechanism is
`shell_environment_policy.set`, so `init --agents codex` writes a marker-delimited
managed block into `config.toml`:

```toml
# dotagents:begin
[shell_environment_policy]
set = {AGENT = "codex", AGENTS_HARNESS = "codex", AGENTS_VENDOR = "openai", AGENTS_HOME = "...", AGENTS_PROJECT_ROOT = "...", AGENTS_PYTHON = "..."}
# dotagents:end
```

Consequences worth knowing:

- **The values are frozen at `init` time.** Change your env layers and Codex keeps the
  old values until you re-run `dotagents init`. There is no way around this.
- The block is **appended** and refreshed in place; content outside the markers — the
  rest of your config — is never touched. Add your own settings outside the markers.
- `set` **merges** on top of what `inherit` admits, so your existing environment is
  unaffected.
- The identity vars describe **the agent the file is for**, not whoever ran the
  command: `dotagents init --agents codex` from a Claude session still writes
  `AGENT = "codex"`.
- `PATH` is deliberately **excluded** — a machine-specific absolute list that, since
  `set` overrides per subprocess, would replace the inherited `PATH` of everything
  Codex spawns.

Other agents (Gemini, Cursor, Copilot) are not wired: without a verified hook schema,
inventing one is how a silently-broken hook gets shipped.

## overlays

Manages opt-in overlays **by name**. See [Overlays](overlays.md) for the full model.

```bash
dotagents overlays add python engineering  # install into the scope, publish skills
dotagents overlays list                    # installed (discovered) + available
dotagents overlays sync 'py*'              # refresh installed overlays matching a glob
dotagents overlays remove python           # delete the overlay dir, unpublish its skills, un-merge its rules
dotagents overlays sync --overwrite        # also replace installed files whose content changed upstream
dotagents overlays show python             # describe one: manifest, requires, setup, skills (--json)
```

`add` installs what each manifest's `requires` names first (`--no-requires` to skip),
validates every name before touching anything, and publishes skills from the installed
copy. `remove` recomposes `AGENTS.md`'s managed block over the overlays that remain, so
an overlay's rules and routing leave with it. `sync` never clobbers an installed file
unless `--overwrite`.

## context

Assembles the effective context an agent should load — the overlay `CONTEXT.md`s
(by priority), then the store's and the project's `AGENTS.md` / `AGENTS.local.md`,
minus whatever the harness already loads by itself (for Claude Code, whatever its
`CLAUDE.md` files really `@`-include) — and prints it to **stdout** by default
(POSIX convention); pass a path to write a file, or `--write-agent` to merge it into
each agent's own instruction file.

```bash
dotagents context                              # print the active agent's context to stdout
dotagents context out.md                       # write it to out.md (positional path)
dotagents context --format json --agents claude   # JSON to stdout
dotagents context --inline                     # also inline the on-demand .md files it points at
dotagents context --write-agent --agents codex # merge a managed block into <project>/AGENTS.md
```

- `[output]` — positional destination. Default `-` (stdout); a path writes that file.
- `--write-agent` — merge the context into each agent's own instruction file under
  the project root, as a managed `dotagents:context` block refreshed in place:
  Claude `.claude/CLAUDE.md`, Codex `AGENTS.md`, Gemini `GEMINI.md`, Cursor
  `.cursorrules`, Copilot `.github/copilot-instructions.md`, Antigravity
  `.agents/rules/dotagents.md`. Prefer the hook where the harness has one; this is
  the static alternative. Mutually exclusive with `[output]` and `--format json`.
- `--inline` — also append the on-demand `.md` files the sources reference (bare or
  backticked relative paths). Off by default: the base rules say to read those
  only when a task needs them, and inlining every mention makes a very large
  session payload.
- `--agents <a,b>` — which agents to generate for (default: the active agent).
- `--format markdown|system-reminder|json` — output shape.
- `-g` / `--global` — skip the project-level context files (the store is unaffected).
- `--agents-dir <dir>` — user store override for this run.

Roots: the store is `--agents-dir` → `$AGENTS_HOME` → `~/.agents`, and the project
root is `$AGENTS_PROJECT_ROOT` → `$CLAUDE_PROJECT_DIR` → the cwd. A `SessionStart`
hook runs this from wherever the session started, so pinning
`AGENTS_PROJECT_ROOT` is what keeps the assembled context the same in every
subdirectory.

## env

Assembles the chained env-file layers (overlays → system → user → project) plus the
standardized identity vars, later-overrides-earlier, and prints them.

```bash
python -m dotagents env --format export -g     # shell-eval'able `export KEY="value"` lines
python -m dotagents env --diff --format json   # only vars that differ from the caller's env
```

- `--format` — output syntax. Default `auto` detects the calling shell (via the
  parent-process chain) and emits sourceable output for it. Shell forms:
  `export` (aliases `posix`/`sh`/`bash`, `export KEY="value"`), `dotenv` (`env`,
  bare `KEY=value`), `powershell` (`pwsh`/`ps`, `$env:KEY = 'value'`), `cmd`
  (`bat`/`batch`, `set "KEY=value"`), `fish` (`set -gx KEY value`). Data forms:
  `json`, `ini`, `yaml`. An explicit `--format` always wins.
- `--diff` — emit only the change set vs. the caller's environment.
- `-g` / `--global` — skip the project-level env files (the store is unaffected).
- `--agents-dir <dir>` — user store override for this run.

Roots resolve exactly as for `context` above (`$AGENTS_HOME` /
`$AGENTS_PROJECT_ROOT`, both of which `env` also emits — so a subprocess reading
them agrees with the parent that wrote them). `env` also emits `AGENTS_PYTHON`,
the interpreter `dotagents` itself is running under — the Python a shim or helper
script should run with, since a bare `python`/`python3` on `PATH` may be a Store
stub, an emulated build, a venv's, or missing (only if unset, so a pin holds;
the env-layer `env.py` scripts run under a valid pin too). And `env` emits one
`<NAME>_OVERLAY_ROOT` per installed overlay, where `NAME` is the overlay's
directory name upper-cased with `-`/`.` turned into `_` (`my-ov` →
`MY_OV_OVERLAY_ROOT`). It is the same name `context` expands as a
`<NAME_OVERLAY_ROOT>` placeholder, so an env file and a context file refer to
an overlay's install dir by one name. Every root is seeded before the env-file
chain and only if unset, so a value pinned upstream holds.

`PATH` and `PYTHONPATH` are built the same way, also before the chain: every
level's `bin/` (overlays, then the store, then the project's `.agents/`) is
prepended to `PATH`, and every level's `lib/` that exists is prepended to
`PYTHONPATH` — so an `env.py`, and every subprocess that inherits the env, can
call an overlay's helpers by name and `import` an overlay's `lib/` module.

!!! warning
    `env` output is sensitive by design — it prints resolved values. Treat the
    output as secret. The command itself never logs `DOTAGENTS_*` / `AGENTS_*`
    values (Leakage rule).

## findings

A per-scope **findings queue**: a finding is a problem execution discovered (the
config or the code caused a mistake or rework) recorded so triage can fold it into
a rule later, instead of an agent editing config mid-task. Each finding is one
markdown file, like an agent memory: a short frontmatter (`name`, `description`,
`status`, `created`) that becomes the index line, and a body with the details.

```bash
dotagents findings add "bare kb/X.md refs were never inlined" -b "what happened, evidence"
dotagents findings list                     # active findings: `name: description`
dotagents findings list --all --json        # active + processed, machine-readable
dotagents findings show <name>              # the whole file (--json for a structured form)
dotagents findings done <name> -r "fixed in _context; test added"
dotagents findings reopen <name>
dotagents findings remove <name>            # for a finding recorded by mistake
dotagents findings index                    # regenerate INDEX.md after hand edits
dotagents findings path                     # where this scope's queue lives
```

- Scope: the project by default (`<project>/.agents/findings/`), the user store
  with `-g` / `--agents-dir` (`<store>/findings/`), or any directory with `--dir`
  (e.g. a queue kept at an older config's `dotagents/findings/`). Roots resolve as
  for every other scope-aware command (`$AGENTS_PROJECT_ROOT`, `$AGENTS_HOME`).
- The base config's own rules use it: a global-config miss is
  `dotagents findings add -g ...`, triage reads `list -g` / `show -g` and closes
  with `done -g` (see the installed `AGENTS.md` and `dotagents/DECISIONS.md`).
- Layout is the queue discipline: active findings at the top level; `done`
  appends a `## Resolution` section and **moves** the file to `processed/`
  (never deletes). The resolution is required — a processed finding without one
  is untriaged, not done. `INDEX.md` (active first, then processed, one line
  each) is regenerated by every mutating command.
- `--body` / `--body-file` (`-` = stdin) carry the details on `add`;
  `--resolution` / `--resolution-file` on `done`. A hand-written note without a
  frontmatter is still listed (name = file stem, description = its first heading
  or line) and gains one the first time the command rewrites it.
- The command is bundled with dotagents and discovered from the package itself;
  a same-named `findings.py` in a scope's `dotagents/cmds/` overrides it.

## launch

Start an agent's command-line harness the way a session wired by `init` would
find the world already: the full `env` exported, and the `context` handed over
up front.

```bash
dotagents launch claude -- --model sonnet   # everything after `--` is the agent's
dotagents launch                            # the active agent, as `context` picks it
dotagents launch codex --dry-run            # print the command line and the exported names
dotagents launch -g gemini                  # user-store tiers only (the env/context meaning of -g)
```

What happens, in order:

1. **Environment** — the same assembly as `dotagents env` for this scope (identity
   vars, `AGENTS_HOME` / `AGENTS_PROJECT_ROOT` / `AGENTS_PYTHON`, every
   `<NAME>_OVERLAY_ROOT`, the `PATH` / `PYTHONPATH` prepends, the env-file chain)
   is applied to the `dotagents` process and handed to the child, so the harness
   and everything it spawns see it.
2. **Context** — `dotagents context` for that agent (what its harness does not
   already load) is written to a file, exported as `AGENTS_CONTEXT_FILE`, and
   passed the way the harness takes appended system-prompt text: Claude Code gets
   `--append-system-prompt-file`. A harness with no append flag (Codex, Gemini,
   Cursor, Copilot — their prompt-file options *replace* the built-in prompt, which
   is not the same thing) gets it the static way instead: merged as the managed
   `dotagents:context` block into its own instruction file in the project, exactly
   what `context --write-agent` does. `--no-context` skips this; `--inline` also
   inlines the on-demand files the sources reference.
3. **The harness** — the agent's program (`claude`, `codex`, `gemini`,
   `cursor-agent`, `copilot`), resolved on the PATH from step 1 so a harness an
   overlay's `bin/` provides is found, or `--command <program>` for one under
   another name. dotagents' flags come first and the passthrough after, so yours
   win where the harness takes the last value. The exit code is the harness's.

`--dry-run` prints the command line and the names of the exported changes (never
their values) and runs nothing. Like `findings`, the command is bundled and
discovered from the package; a same-named `launch.py` in a scope's
`dotagents/cmds/` overrides it.

## audit — not a dotagents command

There is no `dotagents audit`. The dotagents source repo has its own
`tools/audit.py`, but every path it checks is a path in *that repo*
(`src/dotagents/_overlay/…`, `tools/…`), so it validates the repo's layout in CI —
it is not a validator for an installed `~/.agents` and is deliberately not shipped
in the package or the `.pyz`.

### Personal pre-push scanning (not in this repo)

Scanning a repo for personal leaks before publishing it — machine paths, private
plan names, `.agents/` refs, agent-session trailers in commit messages — enforces
personal conventions rather than dotagents' own mechanism, so no such tool is
shipped here. Keep one as a discovered command module in your own private
`<scope>/dotagents/cmds/` and run it locally before a push.

## link-project / sync-project

The optional per-project private-store workflow. These are **not** dotagents
commands: they are supplied by the `private-sync` overlay, together with the logic
behind them, so a plain install carries no private-sync workflow at all. Install the
overlay first and they become available like any other subcommand:

```bash
python -m dotagents overlays add private-sync --repo <overlays-checkout>/overlays

python -m dotagents link-project .                       # symlink this project's .agents into its store
python -m dotagents link-project . --copy                # real-dir copy (no-symlink systems)
python -m dotagents sync-project -m "msg"                # reconcile + hand off to the store's sync
python -m dotagents sync-project --remote <url> -m init  # one-command bootstrap
```

(They were called `link` / `sync` before; the names now say what they act on — a
project's `.agents` — and `sync-project` no longer reads like `overlays sync`.)

See [Private sync](private-sync.md) for the full walkthrough.

## build-pyz

```bash
python -m dotagents build-pyz --out dist/dotagents.pyz
```

Builds a self-contained zipapp with the runtime deps and required tools bundled, so it
runs with no `pip install`.
