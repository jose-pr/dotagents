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
| `link` | Symlink (or copy) a project's `.agents` into its store. |
| `sync` | Reconcile a copy-mode project and hand off to the store's sync path. |
| `build-pyz` | Build the self-contained `dotagents.pyz` zipapp. |

`link` and `sync` are **discovered** command modules (D76), shipped in the package's
bundled command dir — they behave like any other subcommand. Additional command
modules are discovered from each **installed overlay's** `cmds/` dir, each scope's
command dir, `$AGENTS_CMDS_PATH` entries, and `--cmdspath` — one Contract-A resolver
walk covers the overlay + scope tiers (D84). `leak-check` is not built in: it is a
personal command module you drop into your private `<scope>/dotagents/cmds/`, where
it is discovered like any other.

## init

See [Install](install.md) for the full walkthrough. In brief:

```bash
dotagents init                          # base config into <cwd>/.agents (project scope)
dotagents init -g                       # ...into ~/.agents (user scope)
dotagents init --bin-dir ~/.local/bin   # also write a `dotagents` command on PATH
dotagents init --dry-run
```

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

## link / sync

The optional per-project private-store workflow. See
[Private sync](private-sync.md).

```bash
python -m dotagents link .                          # symlink this project's .agents into its store
python -m dotagents link . --copy                   # real-dir copy (no-symlink systems)
python -m dotagents sync -m "msg"                   # reconcile + hand off to the store's sync
python -m dotagents sync --remote <url> -m init     # one-command bootstrap
```

## build-pyz

```bash
python -m dotagents build-pyz --out dist/dotagents.pyz
```

Builds a self-contained zipapp with the runtime deps and required tools bundled, so it
runs with no `pip install`.
