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
| `overlays` | Manage opt-in overlays by name: `add` / `remove` / `list` / `sync`. |
| `context` | Assemble the effective context for one or more agents. |
| `env` | Assemble the chained env-file layers + identity vars, in a chosen format. |
| `build-pyz` | Build the self-contained `dotagents.pyz` zipapp. |

That table is the **whole** shipped surface: dotagents bundles no command module of
its own. Everything else is **discovered**, from each **installed overlay's** `cmds/`
dir, each scope's command dir, `$AGENTS_CMDS_PATH` entries, and `--cmdspath` — one
Contract-A resolver walk covers the overlay + scope tiers (D84). Two consequences
worth knowing:

- `link-project` / `sync-project` (the per-project private-store workflow) come from
  the opt-in **`private-sync` overlay**, which ships the commands and their logic
  together — install it and they appear (see [link-project / sync-project](#link-project--sync-project)).
- `leak-check` is a personal command module you drop into your private
  `<scope>/dotagents/cmds/`, where it is discovered like any other.

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
| `SessionStart` | `dotagents context` | Claude injects a SessionStart hook's **stdout into the session context** — this is how the assembled context reaches the model without you running anything. |
| `CwdChanged` | `[ -f AGENTS.md ] && cat AGENTS.md \|\| true` | Surfaces a directory's `AGENTS.md` when the agent changes into it. |

The merge is additive and idempotent: unrelated settings keys and hooks you wrote
yourself are preserved verbatim, and re-running `init` writes nothing.

The skills link is what makes published skills visible — publishing into
`<scope>/skills/` only helps if the agent reads that directory. It is a symlink where
the OS permits one and a **copy** otherwise (notably Windows without Developer Mode).
A copy is a point-in-time snapshot: re-run `dotagents init` to refresh it after
overlay skills change.

!!! note
    No environment-variable hook is written. The `CLAUDE_ENV_FILE` variable a
    SessionStart hook would need no longer exists in Claude Code, and the documented
    alternative — the static `env` key in `settings.json` — cannot carry computed
    values. Use `dotagents env` from your shell instead.

## overlays

Manages opt-in overlays **by name**. See [Overlays](overlays.md) for the full model.

```bash
dotagents overlays add python flows        # install into the scope, publish skills
dotagents overlays list                    # installed (discovered) + available
dotagents overlays sync 'py*'              # refresh installed overlays matching a glob
dotagents overlays remove python           # delete the overlay dir + unpublish its skills
```

## context

Assembles the effective context an agent should load and prints it to **stdout** by
default (POSIX convention); pass a path to write a file, or `--write-agent` to write each
agent's native config file.

```bash
dotagents context                              # print the active agent's context to stdout
dotagents context out.md                       # write it to out.md (positional path)
dotagents context --format json --agents claude   # JSON to stdout
dotagents context --write-agent                # write each agent's native config file
```

- `[output]` — positional destination. Default `-` (stdout); a path writes that file.
- `--write-agent` — write each agent's native config file instead of `[output]`.
- `--agents <a,b>` — which agents to generate for (default: the active agent).
- `--format markdown|system-reminder|json` — output shape.
- `-g` / `--global` — user scope.

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
- `-g` / `--global` — user scope.

!!! warning
    `env` output is sensitive by design — it prints resolved values. Treat the
    output as secret. The command itself never logs `DOTAGENTS_*` / `AGENTS_*`
    values (Leakage rule).

## audit — not a dotagents command

There is no `dotagents audit`. The dotagents source repo has its own
`tools/audit.py`, but every path it checks is a path in *that repo*
(`src/dotagents/_overlay/…`, `tools/…`), so it validates the repo's layout in CI —
it is not a validator for an installed `~/.agents` and is deliberately not shipped
in the package or the `.pyz`.

### leak-check (personal, not in this repo)

`leak-check` scans any repo before publishing it — personal machine paths, private
plan names, `.agents/` refs, `Phase N` phrasing, and `Claude-Session` trailers. It
enforces personal conventions rather than dotagents' own mechanism, so it is **not**
shipped here: it lives as a discovered command module in your own private
`<scope>/dotagents/cmds/`, run locally before a push.

```bash
python -m dotagents leak-check .                # tracked files + commit messages
python -m dotagents leak-check --commits-only . # commit messages only
```

## link-project / sync-project

The optional per-project private-store workflow. These are **not** dotagents
commands: they are supplied by the `private-sync` overlay, together with the logic
behind them, so a plain install carries no private-sync workflow at all. Install the
overlay first and they become available like any other subcommand:

```bash
python -m dotagents overlays add private-sync --source <overlays-checkout>

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
