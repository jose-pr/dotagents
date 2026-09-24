"""Helpers shared by several test modules (tests only; not a test module)."""


def run_cmd(cmd_cls, *, passthrough=None, **fields):
    """Run a duho command class the way the tests drive one without a parser:
    set each field on a fresh instance, then call it. ``passthrough`` is the
    argv after ``--`` (``launch``). Returns the command's exit code."""
    cmd = cmd_cls()
    if passthrough is not None:
        cmd._passthrough_ = list(passthrough)
    for name, value in fields.items():
        setattr(cmd, name, value)
    return cmd()


def py_emit(mapping) -> str:
    """The body of an ``env.py`` that unconditionally prints ``mapping`` as
    its JSON object of env changes."""
    return "import json\nprint(json.dumps(%r))\n" % (mapping,)
