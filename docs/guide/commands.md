# Commands

The `dotagents` CLI is an umbrella of subcommands. Run it as the installed
`dotagents` wrapper, as `python -m dotagents`, via the `python install.py <cmd>`
dev shim (from a source checkout), or from a built `dotagents.pyz`. Most commands
take a scope flag: **project** by default or **user** with `-g` / `--global` (the
`~/.agents` store, configurable with `$AGENTS_HOME`).

Which project, for the commands that write (`init`, `overlays`, `findings`): a pinned
`$AGENTS_PROJECT_ROOT` (then `$CLAUDE_PROJECT_DIR`) while the current directory is
inside it; outside it, the nearest directory up with a `.git` or its own `.agents`,
else the current directory, with a warning; with no pin, the current directory — so
run them from the project root. `env`, `context` and `launch` always use the pinned
root when there is one (else the current directory), so what they assemble is the
same in every subdirectory. `--agents-dir` names the store the scope resolves to: the
project store for `init` / `overlays` / `findings` without `-g`, the user store
otherwise.

## Command set

| Command | What it does |
| --- | --- |
| `init` | Lay down the neutral base config; block-merge `AGENTS.md` (its first line names the file's own path); wire the agents' includes and hooks; `--bin-dir` also writes a PATH wrapper. |
| `overlays` | Manage opt-in overlays by name: `add` / `remove` / `list` / `sync` / `show`. |
| `context` | Assemble the effective context for one or more agents. |
| `env` | Assemble the chained env-file layers + identity vars, in a chosen format. |
| `path` | Print the bin dirs `env` puts on `PATH` (or, with `--lib`, the lib dirs it puts on `PYTHONPATH`). |
| `build-pyz` | Build the self-contained `dotagents.pyz` zipapp (from a source checkout). |
| `about` | `dotagents-cli <version>`, then the packages bundled in the `.pyz` (or installed beside a plain install) with their versions. |
| `findings` | Per-scope findings queue: `add` / `list` / `show` / `done` / `reopen` / `remove` / `index` / `path`. |
| `launch` | Start an agent's CLI with the `env` exported and the `context` handed over; everything after `--` goes to the agent. |

That table is the **whole** shipped surface: six built-in commands and two bundled
command modules, `findings` and `launch` (discovered from the package itself). Every
other command is **discovered** from a directory of command modules. The sources, in
order — a later one overrides a same-named command from an earlier one:

1. the built-in commands;
2. the bundled `findings` and `launch`;
3. store by store — the system store, the user store, then (in a project) the
   project's `.agents/` — each store's installed overlays' `cmds/` first, then the
   store's own `dotagents/cmds/`;
4. the directories in `$AGENTS_CMDS_PATH` (`os.pathsep`-separated);
5. `--cmdspath <dir>` (repeatable).

So a command an overlay ships is overridden by one in the same store's
`dotagents/cmds/`, a user-store command by a project one, and any of them by
`$AGENTS_CMDS_PATH` or `--cmdspath`. A checkout's own top-level `dotagents/cmds/` (outside
`.agents/`) is never a source. Two consequences worth knowing:

- `link-project` / `sync-project` (the per-project private-store workflow) come from
  the opt-in **`private-sync` overlay**, which ships the commands and their logic
  together — install it and they appear (see [link-project / sync-project](#link-project-sync-project)).
- A personal command module dropped into a scope's `dotagents/cmds/` is discovered
  like any other, so tooling you keep private never has to live in this repo.

Your own commands work the same way: drop a `*.py` defining a `duho` command class
into `<store>/dotagents/cmds/` (create the dir — `init` does not) and it becomes a
subcommand, no registration needed; see
[Authoring → Custom commands](authoring.md#custom-commands).

## init

See [Install](install.md) for the full walkthrough. In brief:

```bash
dotagents init                          # base config into ./.agents (project scope)
dotagents init -g                       # ...into ~/.agents (user scope)
dotagents init --bin-dir ~/.local/bin   # also write a `dotagents` command on PATH
dotagents init --no-hooks               # skip agent hook wiring + the skills links
dotagents init --agents claude,codex    # exactly these agents (default: detected + claude)
dotagents init --dry-run
```

### Hook wiring

For each active agent, `init` adds what that harness needs to load the config, and
`--no-hooks` skips the hooks and skill links. Every merge is additive and idempotent:
unrelated settings keys and hooks you wrote yourself are preserved verbatim, a hook
that is already what `init` would write is left alone, and re-running `init` writes
nothing. Each hook runs `dotagents` by path — the project's `.agents/bin` wrapper, else
the store's, else `dotagents` on `PATH`.

**Claude Code.** Hooks go into `settings.json` in Claude's config dir
(`$CLAUDE_CONFIG_DIR`, else `~/.claude`) for the user store, and into the project's
`.claude/settings.local.json` — the uncommitted file — for a project store.

| Hook | Command | Why |
| --- | --- | --- |
| `SessionStart` | writes `dotagents env --diff --format export` into `$CLAUDE_ENV_FILE` (`--into`: one block, replaced on every run; `PATH` as a prefix on the shell's own), then runs `dotagents context` | Claude sources `$CLAUDE_ENV_FILE` before each Bash command, so the env layers reach every command in the session; and it injects the hook's **stdout into the session context**, which is how the assembled context reaches the model. |
| `CwdChanged` | when the new directory has a `.agents/`, appends `export AGENTS_PROJECT_ROOT=<it>` to `$CLAUDE_ENV_FILE`; then prints the directory's `AGENTS.md` if there is one | Re-pins the project root after a `cd` into another project, and surfaces that directory's `AGENTS.md`. |

On Windows each event gets a second, PowerShell-native handler (`shell:
"powershell"`) that runs only when Claude Code would find no Git Bash, so a machine
with Git Bash does not get the context twice. The PowerShell `SessionStart` handler is
context-only: `$CLAUDE_ENV_FILE` feeds Bash commands only. On other platforms `init`
removes PowerShell handlers an earlier run wrote.

Claude Code's PowerShell tool never reads `$CLAUDE_ENV_FILE`, so its commands do not
see the env layers. On Windows, `init --powershell-env-hook` adds a `PreToolUse` hook
(matched on the `PowerShell` tool) that prepends the `dotagents env` change set to each
PowerShell call (`env --diff --cache`, so an unchanged environment is not reassembled
on every call). **It auto-approves every PowerShell tool call**: rewriting a command
without a prompt on each one requires `permissionDecision: "allow"`, which skips the
approval prompt (your deny and ask rules still apply). It is off by default, and a
plain `init` removes one an earlier release wired.

The env half **appends** (`>>`) and is guarded by `[ -n "$CLAUDE_ENV_FILE" ]`, both
per the [hooks docs](https://code.claude.com/docs/en/hooks#persist-environment-variables):
other hooks write to the same file, so `>` would discard their variables, and an
unguarded redirect would create a file literally named `""` where the variable is
unset. `$CLAUDE_ENV_FILE` exists only inside SessionStart/Setup/CwdChanged/FileChanged
hook processes — it is absent from the session's own shell, so checking for it with
`env | grep` proves nothing.

Skills are linked **one directory per skill**: each `<store>/skills/<name>` becomes
`<Claude config>/skills/<name>`, so a skills directory of your own is never replaced,
and a same-named skill you placed there yourself is kept (with a warning). A link is a
symlink where the OS permits one, else on Windows a directory junction, else a
**copy**. `init` and `overlays add` / `sync` / `remove` (once `init` has wired Claude
for that store) keep them current: a copy you have not edited is refreshed, a skill
the store no longer has is removed, and a copy you edited is kept with a warning.
Which entries are dotagents' is recorded in `<Claude config>/skills/.dotagents-linked.json`.
In a project, `init` warns when those skills are not gitignored.

**Codex** — user store (`-g`) only, since its hooks file is global. `init` deploys two
small Python scripts into `<codex-home>/hooks/` (`$CODEX_HOME`, else `~/.codex`), run
by the interpreter `init` ran with so they work under any shell, and wires them in
`<codex-home>/hooks.json` — never `config.toml`, so your main config is not rewritten
(if you keep inline `[hooks]` there, Codex warns about the split; use `--no-hooks` and
add the hooks yourself):

- `SessionStart` prints `dotagents context --agents codex`; Codex adds a SessionStart
  hook's stdout as developer context.
- `PreToolUse`, matched on Codex's `Bash` tool, prepends the `dotagents env` change set
  to each command through `updatedInput`, so the env is live. On native Windows Codex
  runs commands through PowerShell, which cannot parse that prefix, so the hook passes
  commands through unchanged: Windows Codex sessions get the context but not the env.

Earlier releases wrote a static `[shell_environment_policy]` snapshot into your
`config.toml` on `init --agents codex`. It pinned one project's paths into the
global config and could produce invalid TOML, so it is gone: `init` removes a block
it left there (between `# dotagents:begin` / `# dotagents:end`) and touches nothing
else in the file.

**Antigravity** — only with an explicit `--agents antigravity` (it sets no variable
dotagents could detect it by), and user store (`-g`) only. `init` deploys a script
into `~/.gemini/config/hooks/` and wires it as a `PreInvocation` hook under a
`"dotagents"` entry in `~/.gemini/config/hooks.json`. The hook fires every model turn
and sends the context on the first one only. It carries no env: Antigravity's
`PreToolUse` cannot rewrite a command.

**Gemini CLI, Cursor, Copilot, pi** — no hooks. Gemini CLI gets an `@` import of the
store's `AGENTS.md` in its `GEMINI.md`, pi a pointer in its config dir (user store
only); all four get the context through `dotagents launch` or
`dotagents context --write-agent`.

## overlays

Manages opt-in overlays **by name**. See [Overlays](overlays.md) for the full model.

```bash
dotagents overlays add python engineering  # install into the scope, publish skills, merge rules/routing
dotagents overlays list                    # installed (discovered) + available from the repos
dotagents overlays sync 'py*'              # refresh installed overlays matching a glob
dotagents overlays sync --prune            # also clear what .gitignore/.ignore protect: exactly upstream
dotagents overlays remove python           # delete the overlay dir, unpublish its skills, un-merge its rules
dotagents overlays show python             # describe one: manifest, requires, setup, skills, source (--json)
```

- **`add`** installs what each manifest's `requires` names first, each requirement
  installed and set up before the overlay that needs it (`--no-requires` to skip); a
  requirement the scope already has, in its own store or the user store for a project,
  is satisfied and left alone, while a name you ask for explicitly is always
  (re)installed and set up. It validates and resolves every name against the repos
  before touching anything — the first repo that offers a name wins — records where
  each overlay came from, and publishes skills from the installed copy, never
  replacing a skill already in the shared `skills/` dir (and links them into
  Claude's skills dir when `init` wired Claude for the store). A fresh install whose setup
  script fails is rolled back. A source inside the store's own `overlays/` directory
  is refused (by `sync` too): `remove` would delete the only copy.
- `add`, `remove` and `show` without an overlay name are usage errors (exit 2), and
  a `--dry-run` says what it would write (`would recompose ...`) instead of reporting
  it as done.
- **`sync`** refreshes each installed overlay from the repo it was installed from
  (recorded in `<overlay>/.dotagents-install.json`); `--repo` replaces that recorded
  source for the run. The install is made to match the source exactly: new and changed
  files land, `overlay.toml` is refreshed, and files the source does not ship are
  removed. A file you edited (or added) there is copied to
  `<store>/install_backup/<timestamp>/overlays/<name>/` before it is replaced or
  removed. What the overlay's `.gitignore` / `.ignore` files match — setup output,
  local state — and tool caches (`__pycache__`, `.venv`, …) are the install's own and
  left alone; `--prune` clears them too, leaving exactly the upstream files. A
  `requires` added upstream is installed, and published skill copies are refreshed.
  `add` of an overlay already installed does the same (with `--prune` too). Keep your
  own additions outside `<store>/overlays/` — a `kb/` file of your own, an
  `AGENTS.local.md` — where a sync never touches them.
- **`remove`** deletes the overlay's directory, unpublishes the skills it published
  (and their links in Claude's skills dir), and recomposes `AGENTS.md`'s managed block over the overlays that remain, so its
  rules and routing leave with it. It refuses an overlay another installed overlay
  requires (and no other store provides) unless `--force`.
- **`list`** shows the installed overlays per store (in a project, the user store's
  too; a same-named project overlay shadows the store's copy), flags a `requires` no
  installed overlay provides, and lists what the repos offer (`*` = installed).
  **`show`** also names the repo an installed overlay came from.

## context

Assembles the effective context an agent should load — the overlay `CONTEXT.md`s
(by priority), then each store's `AGENTS.md`, the project store's `AGENTS.local.md`,
and the project root's own `AGENTS.md` / `AGENTS.local.md` — minus whatever the harness already loads by itself (for Claude Code, whatever its
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

- `[output]` — positional destination. Default `-` (stdout); a path writes that
  file, for one agent (several agents need stdout or `--format json`).
- `--write-agent` — merge the context into each agent's own instruction file under
  the project root (not with `-g`), as a managed `dotagents:context` block refreshed
  in place:
  Claude `.claude/CLAUDE.md`, Codex `AGENTS.md`, Gemini `GEMINI.md`, Cursor
  `.cursor/rules/dotagents.mdc` (an `alwaysApply: true` rule), Copilot
  `.github/copilot-instructions.md`, pi
  `.pi/APPEND_SYSTEM.md` (the file pi appends to its system prompt), Antigravity
  `.agents/rules/dotagents.md`. Prefer the hook where the harness has one; this is
  the static alternative. Mutually exclusive with `[output]` and `--format json`.
- `--inline` — also append the on-demand `.md` files the sources reference (bare or
  backticked relative paths, and `$<NAME>_OVERLAY_ROOT/...` / `<PROJECT_ROOT>/...`
  references). Each reference resolves against the directory of the source that
  mentions it; a project's references also search its `.agents`, and a user or
  system source never resolves into the project. Off by default: the base rules
  say to read those only when a task needs them, and inlining every mention
  makes a very large session payload.
- `--agents <a,b>` — which agents to generate for (default: the active agent);
  names or harness ids (`claude-code`). Exits 2 when none is known.
- `--format markdown|system-reminder|json` — output shape.
- `-g` / `--global` — skip the project-level context files (the store is unaffected).
- `--agents-dir <dir>` — user store override for this run.

On stdout and in JSON, `$<NAME>_OVERLAY_ROOT` is expanded to the installed overlay's
directory, so a harness without the env still finds the file; `--write-agent` keeps
the variable, because its file may be committed. Skills are listed with the path of
their `SKILL.md` (JSON: a `path` key), a project skill winning over a same-named user
one.

Roots: the store is `--agents-dir` → `$AGENTS_HOME` → `~/.agents`, and the project
root is `$AGENTS_PROJECT_ROOT` → `$CLAUDE_PROJECT_DIR` → the cwd. A `SessionStart`
hook runs this from wherever the session started, so pinning
`AGENTS_PROJECT_ROOT` is what keeps the assembled context the same in every
subdirectory.

## env

Assembles the chained env layers — store by store (system, user, then the project's
`.agents/`), each store's overlays before the store itself — plus the standardized
identity vars, later-overrides-earlier, and prints them.

```bash
dotagents env --format export -g     # shell-sourceable `export KEY='value'` lines
dotagents env --diff --format json   # only vars that differ from the caller's env
```

- `--format` — output syntax. Default `auto` detects the calling shell (via the
  parent-process chain) and emits sourceable output for it. An explicit `--format`
  always wins.
- `--diff` — emit only the change set vs. the caller's environment.
- `--cache` — replay the previous output while nothing it depends on changed (the
  environment, the working directory, the stores, the overlays, every env and `lib`
  file), for at most five minutes; the per-command env loaders use it. The cache is
  owner-only, under `<user store>/.cache/env/`.
- `--into <file>` — write into `<file>` instead of stdout, as the one block this
  command owns there (between `: dotagents-env-begin` / `: dotagents-env-end`):
  an earlier block is replaced, anything else in the file is kept. The SessionStart
  hook writes `$CLAUDE_ENV_FILE` this way, so the file stays one block however often
  the hook runs. `export` format only.
- `-g` / `--global` — skip the project-level env files (the store is unaffected).
- `--agents-dir <dir>` — user store override for this run.

| Format (aliases) | Output | Notes |
| --- | --- | --- |
| `export` (`posix`, `sh`, `bash`) | `export KEY='value'` | POSIX single quotes (`'` written `'\''`), so nothing in a value is expanded or run when sourced. |
| `dotenv` (`env`) | `KEY=value` | For python-dotenv; quoted only when needed. Read it with `interpolate=False` to keep a `${X}` in a value literal. |
| `powershell` (`pwsh`, `ps`) | `${env:KEY} = 'value'` | Every single-quote character doubled. |
| `cmd` (`bat`, `batch`) | `set "KEY=value"` | `%` doubled: run it as a batch file (`dotagents env --format cmd > env.cmd && call env.cmd`), with delayed expansion off. A newline becomes a space, and a `"` cannot be escaped. |
| `fish` | `set -gx KEY 'value'` | `\` and `'` escaped. |
| `json` | an object | Sorted keys. |
| `ini` | `KEY=value` under `[env]` | For Python's `configparser`; a value it cannot hold verbatim is named in a `;` comment instead. |
| `yaml` | `KEY: "value"` | Every value double-quoted, so none reads back typed. |

A variable an env layer unsets is emitted as a removal: `unset K` (export),
`set -e K` (fish), `${env:K} = $null` (powershell), `set "K="` (cmd), `null` (json,
yaml). `dotenv` and `ini` have no way to say "unset" and leave it out. On Windows the
POSIX formats (`export`, `dotenv`, `fish`) convert `PATH`-like values to `/c/...`
form and drop names that are not shell identifiers.

**What is assembled, in order:**

1. **Identity** — `AGENTS_HARNESS`, `AGENTS_VENDOR`, `AGENT`, `AGENTS_MODEL` for the
   harness running this process (none in a plain shell), never replacing a value
   already set.
2. **Roots** — `AGENTS_HOME` and `AGENTS_PROJECT_ROOT` name the stores this run
   actually walked, as absolute paths: an inherited value naming the same directory
   holds, any other is replaced (with `-g`, which walks no project,
   `AGENTS_PROJECT_ROOT` is only set when unset). Only when unset: `AGENTS_PYTHON`, the
   interpreter `dotagents` itself runs under — the Python a shim or helper script
   should use, since a bare `python`/`python3` on `PATH` may be a Store stub, an
   emulated build, a venv's, or missing — and one `<NAME>_OVERLAY_ROOT` per installed
   overlay, where `NAME` is the overlay's name upper-cased with `-` turned into `_`
   (`my-ov` → `MY_OV_OVERLAY_ROOT`). That is the same name `context` expands as a
   `<NAME_OVERLAY_ROOT>` placeholder, so an env file and a context file refer to an
   overlay's install dir by one name. When `dotagents` runs from a `.pyz`,
   `AGENTS_PYLIB` names that archive: the libraries it bundles (duho,
   pathlib_next) import from it, so an overlay built on them appends it to
   `sys.path` when its own import fails. An installed `dotagents` sets nothing,
   since those libraries are already in `AGENTS_PYTHON`'s site-packages. It is
   never put on `PYTHONPATH`, where it would shadow a project's own copy.
3. **`PATH`** — every level's `bin/` (each store's overlays, then the store) goes
   to the front in that order, including ones that do not exist yet, so an `env.py`
   and every subprocess can call an overlay's helpers by name. The order holds even
   when the calling shell already had some of them, and empty or relative inherited
   entries are dropped.
4. **`PYTHONPATH`** — every level's `lib/` that exists goes to the front, highest
   precedence first, so every Python the session starts — a skill script, an
   overlay's `bin/` launcher, an `env.py` — can `import` any overlay's `lib/`
   module, including another overlay's. The same list is published as
   `AGENTS_PYTHONPATH` (`os.pathsep`-joined), which is how `env` knows which
   `PYTHONPATH` entries are its own: after a `cd` into another project, the first
   project's libs leave. A `lib/` module comes before site-packages and the
   standard library, so it must not reuse a name it does not mean to replace.
5. **The env files** — first every level's `pre.env.py` / `pre.env` (and the project's
   `pre.local.env`), then every level's `env.py` / `env` (and the project's
   `local.env`), each evaluated against everything before it. A project root's own
   top-level files outside `.agents/` never run.
6. **Proxy** — `AGENTS_PROXY` is set from `AGENTS_WEBFETCH_PROXY_URL` or the global
   `HTTPS_PROXY` / `HTTP_PROXY` / `ALL_PROXY` when unset, and each proxy variable that
   is set in one case is mirrored into the other.

**`env.py`** is executed with the Python in `AGENTS_PYTHON` (else `dotagents`' own),
as `env.py --level <level> [--global]`, where `<level>` is `system`, `user`,
`project` or the overlay's name and `--global` says the project tiers are skipped
(`--agent <level>` is passed too, for older scripts). It inherits everything
assembled so far and prints its changes to stdout as JSON: one object, or one object
per line, merged in order. Each value must be a string; `null` unsets the variable;
any other type, a key that cannot be a variable name (empty, or holding `=` or NUL),
and a value holding NUL or bytes that are not UTF-8 are skipped with a warning naming
the key. A script that exits non-zero or prints something else contributes nothing,
with a warning; values are never logged.

**A plain env file** (`env`, `pre.env`, `local.env`, `pre.local.env`) is sourced by
bash with `set -a`, and whatever it changed, including `unset`, is taken; both
snapshots come from the same bash process. On Windows that is Git for Windows' bash
(not the WSL launcher); with no usable bash, plain env files are skipped with a
warning. A file whose `source` fails, or that calls `exit`, contributes nothing.

!!! warning
    `env` output is sensitive by design — it prints resolved values. Treat the
    output as secret. The command itself never logs `DOTAGENTS_*` / `AGENTS_*`
    values, and its full (non-`--diff`) output leaves inherited `DOTAGENTS_*`
    values out.

## path

The dirs `env` puts at the front of `PATH` — or with `--lib`, of `PYTHONPATH` —
highest precedence first. Nothing is run (no env file, no `env.py`), so it is a
cheap way to put the overlay bins on a shell's `PATH`:

```bash
export PATH="$(dotagents path --format posix):$PATH"   # bash / Git Bash
dotagents path --lib --format json                     # the lib dirs, as JSON
```

- `--format list` (default, one per line), `native` (`os.pathsep`-joined), `posix`
  (`:`-joined; on Windows each dir in its MSYS2 form, `/c/...`), `json`.
- The bin list includes dirs that do not exist yet, as `env` does; the lib list only
  existing ones.
- `-g` / `--agents-dir` mean what they mean for `env`: `-g` leaves the project's dirs
  out, `--agents-dir` names the user store.

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
dotagents findings remove <name>            # an active finding recorded by mistake
dotagents findings index                    # regenerate INDEX.md after hand edits
dotagents findings path                     # where this scope's queue lives
```

- Scope: the project by default (`<project>/.agents/findings/`), the user store
  with `-g` (`<store>/findings/`), or any directory with `--dir` (e.g.
  `--dir ~/notes/findings`). Roots resolve as for every other scope-aware command
  (`$AGENTS_PROJECT_ROOT`, `$AGENTS_HOME`).
- The base config's own rules use it: a global-config miss is
  `dotagents findings add -g ...`, triage reads `list -g` / `show -g` and closes
  with `done -g` (see the installed `AGENTS.md`). The resolution is the record;
  dotagents keeps no separate design log.
- Layout is the queue discipline: active findings at the top level; `done`
  appends a `## Resolution` section and **moves** the file to `processed/`
  (never deletes). The resolution is required — a processed finding without one
  is untriaged, not done. `INDEX.md` (active first, then processed, one line
  each) is regenerated by every mutating command.
- `--body` / `--body-file` (`-` = stdin, read as UTF-8) carry the details on
  `add`; `--resolution` / `--resolution-file` on `done`. A hand-written note
  without a frontmatter is still listed (name = file stem, description = its
  first heading or line) and gains one the first time the command rewrites it;
  a note that is not UTF-8 is skipped with a warning and never rewritten.
- `add` refuses the names `index` and `readme` (on a case-insensitive
  filesystem they are `INDEX.md` / `README.md`). `done` and `reopen` refuse to
  move onto an existing file, and `remove` deletes active findings only: reopen
  a processed one first, so its resolution is not lost by accident.
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
   `<NAME>_OVERLAY_ROOT`, the `PATH` and `PYTHONPATH` prepends, the env-file
   chain) is applied to the `dotagents` process and handed to the child, so the
   harness and everything it spawns see it.
2. **Context** — `dotagents context` for that agent (what its harness does not
   already load) is written to a file in the user store,
   `.cache/launch/<agent>-<hash of the project root>.md` (overwritten by the next
   launch; `--dry-run` uses a temp file), exported as `AGENTS_CONTEXT_FILE`, and
   passed the way the harness takes appended system-prompt text: Claude Code gets
   `--append-system-prompt-file`, pi gets `--append-system-prompt` (on POSIX; on
   Windows its npm `.cmd` shim cannot carry a multi-line argument, so it takes the
   static route below). A harness with no append flag (Codex, Gemini,
   Cursor, Copilot — their prompt-file options *replace* the built-in prompt, which
   is not the same thing) gets it the static way instead: merged as the managed
   `dotagents:context` block into its own instruction file in the project, exactly
   what `context --write-agent` does — but only into a file git does not track, and
   not under `-g`: those files are usually committed, and the context carries your
   user store's private rules. Otherwise the context stays at `$AGENTS_CONTEXT_FILE`
   only, with a warning; `--write-agent` merges it anyway. The harness is resolved
   before anything is written, so a launch that fails with "not found" leaves the
   project untouched. `--no-context` skips this; `--inline` also inlines the
   on-demand files the sources reference.
3. **The harness** — the agent's program (`claude`, `codex`, `gemini`,
   `cursor-agent`, `copilot`, `pi`), resolved on the PATH from step 1 so a harness an
   overlay's `bin/` provides is found, or `--command <program>` for one under
   another name. On Windows the current directory is never searched (a
   `claude.cmd` at a repository's root is not the harness). dotagents' flags come first and the passthrough after, so yours
   win where the harness takes the last value. The exit code is the harness's
   (128+N when a signal N killed it). On Windows a `.cmd` / `.bat` harness runs
   through cmd.exe, which would execute or expand `& | < > ^ % ! "` and cut an
   argument at a newline, so an argument holding one is refused rather than
   passed on rewritten.

`--dry-run` prints the command line, the names of the exported changes (never
their values) and, on an `unset:` line, the variables an env layer removed, and runs
nothing. Those removed variables never reach the harness. `--agents-dir` overrides the user store, as for
`env` and `context`. Like `findings`, the command is bundled and discovered from the
package; a same-named `launch.py` in a scope's `dotagents/cmds/` overrides it.

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
overlay into the user store first and they become available like any other
subcommand:

```bash
dotagents overlays add private-sync -g --repo "https://github.com/jose-pr/dotagents.git@repo#overlays"

dotagents link-project .                       # symlink this project's .agents into its store
dotagents link-project . --copy                # real-dir copy (no-symlink systems)
dotagents sync-project -m "msg"                # reconcile + hand off to the store's sync
dotagents sync-project --remote <url> -m init  # one-command bootstrap
```

(They were called `link` / `sync` before; the names now say what they act on — a
project's `.agents` — and `sync-project` no longer reads like `overlays sync`.)

See [Private sync](private-sync.md) for the full walkthrough.

## about

```bash
dotagents about          # dotagents-cli <version>, then duho, pathlib_next, ...
dotagents about --json   # the same, plus where it runs from and the Python
```

One `<distribution> <version>` per line, the CLI first. From a built `.pyz` the
list is exactly what `build-pyz` vendored (it records the names and versions in
`dotagents/_bundle.json` before stripping the `dist-info` dirs the zipapp would
otherwise carry, so `--extras uri,http` shows up here as `uritools`, `requests`
and friends). From a plain install it is whichever of a fixed set is installed:
`duho`, `pathlib_next`, `uritools`, `netimps` and `requests`. A package outside that
set, such as a client the `sftp` or `s3` extra pulls in, is not listed.

## build-pyz

```bash
python -m dotagents build-pyz --out dist/dotagents.pyz
python -m dotagents build-pyz --extras uri,http     # also vendor pathlib_next's extras
```

Builds a self-contained zipapp from a source checkout, with `duho` and `pathlib_next`
vendored (plus the `pathlib_next` extras named by `--extras`), so it runs with no
`pip install`. The repo's `tools/` never ships in it. Flags:

- `--out <path>` — where to write it (default `dist/dotagents.pyz`).
- `--python <shebang>` — the interpreter line embedded in it (default
  `/usr/bin/env python3`).
- `--duho-version`, `--pathlib-next-version` — the versions vendored (defaults: the
  floors the package declares).
- `--extras <a,b>` — `pathlib_next` extras to vendor too (`uri`, `http`, `s3`),
  so the `.pyz` can read those overlay sources; none by default. Everything is
  vendored as pure-Python wheels resolved for Python 3.9, so the file does not
  depend on the machine that built it; `sftp` needs native modules and fails the
  build with a message saying so. The archive is compressed.

Each release attaches a built one:
<https://github.com/jose-pr/dotagents/releases/latest/download/dotagents.pyz>.
