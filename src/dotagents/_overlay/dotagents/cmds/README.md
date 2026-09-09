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

    built-ins  <  the bundled cmds/ (findings)  <  the store's overlays' cmds/
    <  system  <  user  <  the project's overlays' cmds/  <  project
    <  $AGENTS_CMDS_PATH entries  <  --cmdspath entries

So a command you drop here (user scope) overrides one an overlay ships, and a
project's `.agents/dotagents/cmds/` overrides yours. A module that fails to
import (a syntax error, an exception at import time) is skipped with a
warning naming the file -- it never takes the other commands down with it.

## What dotagents ships here

One bundled command, **`findings`** — a per-scope findings queue
(`dotagents findings --help`: add / list / show / done / reopen / remove /
index / path, over `<scope>/findings/`). It is discovered from the package
itself, so `init` does not copy it into this directory; drop a same-named
`findings.py` here to override it.
