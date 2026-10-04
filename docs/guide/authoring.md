# Authoring an overlay

An overlay is just a directory with the files you want installed into a scope, plus an
optional `overlay.toml` manifest. Anyone can write one; name its parent directory (a
directory of overlays), a registry, or a git repo with `--repo` — see the
[overlays guide](overlays.md). Keep the source outside `<store>/overlays/`:
`overlays remove` deletes the installed directory outright.

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
    "- Working on topic X → `$MY_OVERLAY_OVERLAY_ROOT/kb/MY_TOPIC.md`",
]
```

Install it:

```bash
dotagents overlays add my-overlay --repo /path/to/parent -g
```

The overlay installs as a directory, `~/.agents/overlays/my-overlay/` here, and each
`routing` line is appended to the core's "Load on demand" table so agents know when to
read the file. Refer to your own files through the overlay's root variable,
`$MY_OVERLAY_OVERLAY_ROOT` (the name upper-cased, `-` → `_`, plus `_OVERLAY_ROOT`):
`dotagents env` exports it and `dotagents context` expands it, so the line resolves
wherever the store lives. A bare `kb/MY_TOPIC.md` would name a file that is not there.

## Contributing rules

To add **always-on** rules (not just routing), point `rules` at overlay-relative
markdown files; their `- **…` bullet blocks are appended to the core's "Always-on
rules" section:

```toml
rules = ["rules/my-rules.md"]
```

Only the leading run of bullets is taken, up to the file's next `## ` heading, so the
file can explain itself below that. Keep the core small — rules are always loaded, so
they cost context every session. Put detail behind routing instead.

## Priority

When several overlays contribute to the same merged region, `priority` decides order
(lower sorts earlier; the unprioritized default is 500). Set it only when order
matters.

## Requirements

`requires = ["engineering"]` makes `overlays add` install the named overlays first
(unless the scope already has them), and `overlays remove` refuse to remove one while
yours needs it.

## Libraries and launchers

Put importable Python in `lib/`. Every Python a session starts sees every overlay's
`lib/` on `PYTHONPATH` (`dotagents env` puts them there, highest precedence first,
and lists them in `$AGENTS_PYTHONPATH`), so a skill script or another overlay can
`import` your module. So can your setup script and your command modules, which
dotagents starts with the libs in place whatever shell it runs from. A `lib/` module
comes before site-packages and the standard library: do not reuse a package name you
do not mean to replace.

A `bin/` launcher can also be run outside a `dotagents env` session (from cmd.exe or
a PowerShell prompt, say), so it sets `PYTHONPATH` itself before starting Python:
its own `lib/`, then `$AGENTS_PYTHONPATH`, then the caller's, leaving empty parts
out (an empty entry means the current directory). The net overlay's `bin/curl` and
`bin/curl.cmd` are the pattern: POSIX sh with no external commands, and a `.cmd`
that uses `setlocal` so the caller's cmd session is not changed.

## Setup scripts

Ship an idempotent `setup.py` at the overlay root to run install-time wiring
automatically — the recommended, OS-agnostic form (it runs under the same Python as
dotagents, so it works on every platform). See the contract in
[Overlays → Setup scripts](overlays.md#setup-scripts). The essentials:

- idempotent, check-then-act;
- cwd is your installed overlay dir;
- the environment is the scope's assembled `dotagents env` (every overlay's `lib/` on
  `PYTHONPATH`, `bin/` on `PATH`), plus the user store (`AGENTS_HOME`), the store you are installed
  into (`AGENTS_SCOPE_ROOT`, with `AGENTS_SCOPE` = `user` / `project`) and your own
  installed dir (`AGENTS_OVERLAY_DIR`) — never hardcode a home path;
- it has 300 seconds unless `setup_timeout` in `overlay.toml` (or the user's
  `--setup-timeout`) says otherwise; `0` means no limit;
- a non-zero exit fails the install loudly; confirm any irreversible action yourself.

## Custom commands

A **command module** is a `*.py` file defining a `duho` command class with a
`__call__` entry point, and each one becomes a
`dotagents <name>` subcommand, discovered at run time with no registration. An overlay
ships its modules in its own `cmds/` dir. For your own, create `dotagents/cmds/` in a
store: `~/.agents/dotagents/cmds/` for every session, `<project>/.agents/dotagents/cmds/`
for one project. `init` does not create that directory; you create it when you add your
first module.

```python
# ~/.agents/dotagents/cmds/hello.py
from dotagents.cli import DotAgentsArgs


class Hello(DotAgentsArgs):
    """Say hello, and name the store this command would act on."""

    _parsername_ = "hello"

    who: str = "world"
    "Who to greet."
    ("--who",)

    def __call__(self) -> int:
        scope = self.resolve_scope()  # honours -g / --agents-dir, like every command
        print("hello, %s (store: %s)" % (self.who, scope.agents_root))
        return 0
```

Then `dotagents hello --who you`. `DotAgentsArgs` brings the `-g` / `--agents-dir` pair
and `resolve_scope()`, so a command that works on a store never redeclares them; a
command that needs neither can subclass `duho`'s `LoggingArgs` and `Cmd` directly. A
field's help is the string after it and its flags the tuple after that. Files whose
name starts with `_` are skipped — use that prefix for shared helper modules. Only
module-level command classes become commands: nest the subcommands of an umbrella
command inside it, or they register as top-level commands too.

Sources layer so that a later one overrides a same-named command: the built-ins, then
the bundled `findings` and `launch`, then store by store — the system store (see
below), the user store, the project's `.agents/` — each store's overlays' `cmds/`
before the store's own `dotagents/cmds/`, and last `$AGENTS_CMDS_PATH` entries
(os.pathsep-split) and `--cmdspath` entries. The system store is `/etc/agents` on
POSIX or `$AGENTS_SYSTEM_ROOT` (the only way to have one on Windows), and it counts
only when it exists and only administrators can write it. So your user-store command overrides one shipped by an
overlay installed in the user (or system) store, and a project's overlays and
`.agents/dotagents/cmds/` override yours. A module that fails to
import (a syntax error, an exception at import time) is skipped with a warning naming
it; it never takes the other commands down with it, and neither does a command whose
parser cannot be built. The bundled `findings` and `launch` are ordinary command
modules discovered from the package, and the private-sync overlay ships
`link-project` / `sync-project` from its own `cmds/` the same way.

**Prefer a command module to a loose script.** A script under `tools/` is something
an agent has to find, quote and spawn. A command module is discovered, shows up in
`dotagents --help`, and is served as an MCP tool with a typed schema when `dotagents`
runs with `DOTAGENTS_MCP=stdio` (see
[Commands → As tools for an agent](commands.md#as-tools-for-an-agent)). Write it to be
a good tool:

- the logic lives in the command class — `__call__` and its methods — not in one
  function that `__call__` forwards every field to;
- each field's help says what the value is, since it becomes the schema's description;
- the output is short: one line for a change, the smallest useful part for a read,
  `--json` for a program;
- it never prompts, and a command that deletes or publishes needs an explicit flag.

## Skills

Put `skills/<skill-name>/` directories in your overlay to publish shared skills into
the scope on install. See [Overlays → Skills](overlays.md#skills).

A skill that relies on commands says so, in its own text: which `dotagents` commands
it uses, that they are available as MCP tools (`dotagents.<command>`) when the
server is registered, and the command-line form to fall back to when it is not. An
agent reading the skill should not have to discover either.
