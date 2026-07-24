# Authoring an overlay

An overlay is just a directory with the files you want laid into a scope, plus an
optional `overlay.toml` manifest. Anyone can write one; point `dotagents overlays` at
its parent directory with `--source`.

## Minimal overlay

```
my-overlay/
  overlay.toml
  kb/MY_TOPIC.md
```

```toml
# my-overlay/overlay.toml
name = "my-overlay"
description = "My team's conventions for topic X."
routing = [
    "- Working on topic X → `kb/MY_TOPIC.md`",
]
```

Install it:

```bash
python -m dotagents overlays add my-overlay --source /path/to/parent -g
```

The overlay's files land at the same relative path in the scope, and each `routing`
line is appended additively to the core's "Load on demand" table so agents know when to
read `kb/MY_TOPIC.md`.

## Contributing rules

To add **always-on** rules (not just routing), point `rules` at overlay-relative
markdown files; their `- **…` bullet blocks are appended to the core's "Always-on
rules" section:

```toml
rules = ["rules/my-rules.md"]
```

Keep the core small — rules are always loaded, so they cost context every session. Put
detail behind routing instead.

## Priority

When several overlays contribute to the same merged region, `priority` decides order
(lower sorts earlier; the unprioritized default is 500). Set it only when order
matters.

## Setup scripts

Ship an idempotent `setup.py` at the overlay root to run install-time wiring
automatically — the recommended, OS-agnostic form (it runs under the same Python as
dotagents, so it works on every platform; the bundled `net` overlay is the model). An
extensionless `setup` (a POSIX shell script) still works as a legacy fallback but is
discouraged — it needs a shell, so it isn't portable to Windows. When both are present,
`setup.py` wins. See the contract in
[Overlays → Setup scripts](overlays.md#setup-scripts). The essentials:

- idempotent, check-then-act;
- cwd is your installed overlay dir;
- the environment carries the resolved store path and your own installed dir — never
  hardcode a home path;
- a non-zero exit fails the install loudly; confirm any irreversible action yourself.

## Custom commands

An overlay (or a scope's command dir) can ship **command modules** that become new
`dotagents` subcommands, discovered at run time. A command module defines a
`duho` command class — a `class X(LoggingArgs, Cmd)` with a `__call__` entry point —
and dotagents discovers it from:

- the scope command dirs (user + project), always;
- `$AGENTS_CMDS_PATH` entries (os.pathsep-split);
- `--cmdspath` entries passed on the command line.

Later sources override an earlier same-named command, so a project command can shadow a
user command of the same name. The bundled `link` / `sync` commands are exactly this
mechanism (D76).

## Skills

Put `skills/<skill-name>/` directories in your overlay to publish shared skills into
the scope on install. See [Overlays → Skills](overlays.md#skills).
