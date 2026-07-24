# `dotagents/cmds/` — your own commands go here

`init` creates this directory in your store (`<store>/dotagents/cmds/`). Any
`*.py` file you drop in it that defines a `duho` command class becomes a
`dotagents <name>` subcommand — no registration, no config.

```python
# <store>/dotagents/cmds/hello.py
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

Then `dotagents hello --who you`.

Files whose name starts with `_` are skipped by discovery — use that prefix for
shared helper modules.

## Precedence

Discovery layers sources so a later one overrides a same-named command:

    built-ins  <  installed overlays' cmds/  <  system  <  user  <  project

So a command you drop here (user scope) overrides one an overlay ships, and a
project's `.agents/dotagents/cmds/` overrides yours.

## dotagents itself ships none

This directory is intentionally empty in a fresh install (D85). `link`/`sync`
used to ship here; they are now `link-project`/`sync-project`, shipped by the
opt-in **private-sync** overlay together with their logic — so plain dotagents
carries no private-sync workflow. Install it with:

    dotagents overlays add private-sync --source <overlays-checkout>
