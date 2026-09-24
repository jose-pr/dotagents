# Authoring an overlay

An overlay is just a directory with the files you want laid into a scope, plus an
optional `overlay.toml` manifest. Anyone can write one; name its parent directory (a
directory of overlays), a registry, or a git repo with `--repo` — see the overlays guide.

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
python -m dotagents overlays add my-overlay --repo /path/to/parent -g
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
dotagents, so it works on every platform). See the contract in
[Overlays → Setup scripts](overlays.md#setup-scripts). The essentials:

- idempotent, check-then-act;
- cwd is your installed overlay dir;
- the environment carries the resolved store path and your own installed dir — never
  hardcode a home path;
- a non-zero exit fails the install loudly; confirm any irreversible action yourself.

## Custom commands

A **command module** is a `*.py` file defining a `duho` command class — a
`class X(LoggingArgs, Cmd)` with a `__call__` entry point — and each one becomes a
`dotagents <name>` subcommand, discovered at run time with no registration. An overlay
ships its modules in its own `cmds/` dir. For your own, create `dotagents/cmds/` in a
store: `~/.agents/dotagents/cmds/` for every session, `<project>/.agents/dotagents/cmds/`
for one project. `init` does not create that directory; you create it when you add your
first module.

```python
# ~/.agents/dotagents/cmds/hello.py
from duho import Cmd, LoggingArgs


class Hello(LoggingArgs, Cmd):
    """Say hello."""

    _parsername_ = "hello"

    who: str = "world"
    ("--who",)

    def __call__(self) -> int:
        print("hello, %s" % self.who)
        return 0
```

Then `dotagents hello --who you`. Files whose name starts with `_` are skipped — use
that prefix for shared helper modules. A command that works on a scope inherits
`dotagents.cli.DotAgentsArgs` (the `-g` / `--agents-dir` pair and `resolve_scope()`)
instead of redeclaring the flags.

Sources layer so that a later one overrides a same-named command: the built-ins, then
the bundled `findings` and `launch`, then store by store — system (`/etc/agents`), the
user store, the project's `.agents/` — each store's overlays' `cmds/` before the
store's own `dotagents/cmds/`, and last `$AGENTS_CMDS_PATH` entries (os.pathsep-split)
and `--cmdspath` entries. So your user-store command overrides one shipped by an
overlay installed in the user (or system) store, and a project's overlays and
`.agents/dotagents/cmds/` override yours. A module that fails to
import (a syntax error, an exception at import time) is skipped with a warning naming
it; it never takes the other commands down with it. The private-sync overlay's
`link-project` / `sync-project` are exactly this mechanism.

## Skills

Put `skills/<skill-name>/` directories in your overlay to publish shared skills into
the scope on install. See [Overlays → Skills](overlays.md#skills).
