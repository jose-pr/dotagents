# Commands

The `dotagents` CLI is an umbrella of subcommands. Run it as the installed
`dotagents` wrapper, as `python -m dotagents`, via the `python install.py <cmd>`
shim, or from a built `dotagents.pyz`. Most commands take a scope flag: **project**
by default (the `<cwd>/.agents` store, when run inside a project) or **user** with
`-g` / `--global` (the `~/.agents` store, configurable).

## Command set

| Command | What it does |
| --- | --- |
| `init` | Lay down the neutral base overlay; block-merge `AGENTS.md`/`CLAUDE.md`. |
| `install` | Base overlay + optional PATH wrapper script (`--bin-dir`). |
| `overlays` | Manage opt-in overlays by name: `add` / `remove` / `list` / `sync`. |
| `context` | Assemble the effective context for one or more agents. |
| `env` | Assemble the chained env-file layers + identity vars, in a chosen format. |
| `audit` | Validate a config tree (manifest, forbidden patterns, size budgets). |
| `leak-check` | Scan a repo's tracked files + commit messages for private leakage. |
| `link` | Symlink (or copy) a project's `.agents` into its store. |
| `sync` | Reconcile a copy-mode project and hand off to the store's sync path. |
| `build-pyz` | Build the self-contained `dotagents.pyz` zipapp. |

`link` and `sync` are **discovered** command modules (D76), shipped in the package
and discovered from the store's command dir — they behave like any other subcommand.
Additional user command modules are discovered from each scope's command dir,
`$DOTAGENTS_CMDS_PATH` entries, and `--cmdspath`.

## init / install

See [Install](install.md) for the full walkthrough. In brief:

```bash
python install.py init                 # base config into ~/.agents
python install.py init --dry-run
python install.py install --bin-dir ~/.local/bin   # base + a `dotagents` command
```

## overlays

Manages opt-in overlays **by name**. See [Overlays](overlays.md) for the full model.

```bash
python install.py overlays add python flows        # install into the scope, publish skills
python install.py overlays list                    # installed (discovered) + available
python install.py overlays sync 'py*'              # refresh installed overlays matching a glob
python install.py overlays remove python           # delete the overlay dir + unpublish its skills
```

## context

Assembles the effective context an agent should load and writes it to the agent's
native config file (or stdout).

```bash
python -m dotagents context --agents claude          # write claude's native context
python -m dotagents context --format json --agents claude -o -   # emit JSON to stdout
```

- `--agents <a,b>` — which agents to generate for (default: the active agent).
- `--format markdown|system-reminder|json` — output shape.
- `--out <path>|-` — destination (default: the agent's native config file).
- `-g` / `--global` — user scope.

## env

Assembles the chained env-file layers (overlays → system → user → project) plus the
standardized identity vars, later-overrides-earlier, and prints them.

```bash
python -m dotagents env --format export -g     # shell-eval'able `export KEY="value"` lines
python -m dotagents env --diff --format json   # only vars that differ from the caller's env
```

- `--format export|json|ini|yaml` — `export` is the default a SessionStart hook
  sources.
- `--diff` — emit only the change set vs. the caller's environment.
- `-g` / `--global` — user scope.

!!! warning
    `env` output is sensitive by design — it prints resolved values. Treat the
    output as secret. The command itself never logs `DOTAGENTS_*` / `AGENTS_*`
    values (Leakage rule).

## audit / leak-check

`audit` validates a config tree; `leak-check` scans any repo before publishing it.

```bash
python -m dotagents audit --root .              # validate a checkout
python -m dotagents audit --repo-hygiene .      # no personal leftovers in tracked files
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
