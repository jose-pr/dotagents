#!/usr/bin/env python3
"""Build a self-contained ``dist/dotagents.pyz`` with all dependencies bundled.

Thin root-level wrapper over ``dotagents build-pyz`` (``dotagents.cli.build_pyz``):
it vendors ``duho`` + ``pathlib_next`` into the archive (via ``pip install --target``,
using **the Python that runs this script**), copies the ``dotagents`` package and the
required ``tools/``, strips caches/metadata, and packages a runnable zipapp. The result
needs no ``pip install`` on the target machine.

Usage:
    python build.py                      # -> dist/dotagents.pyz
    python build.py path/to/out.pyz      # explicit output path
    python build.py --out path/to.pyz    # same, explicit flag
    python build.py -- --duho-version X  # pass any build-pyz flag through after `--`

The embedded shebang and the vendoring interpreter are both ``sys.executable`` (the
Python running this), so the pyz targets the same interpreter family you built with.
Run from a source checkout: the ``dotagents`` package must be importable (a plain
checkout resolves it via ``src/`` on ``sys.path``, added below).
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)

    # A bare positional (not starting with `-`) is a convenience for --out.
    out = None
    passthrough = []
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--":
            passthrough.extend(argv[i + 1:])
            break
        if arg in ("--out", "-o"):
            out = argv[i + 1] if i + 1 < len(argv) else None
            i += 2
            continue
        if not arg.startswith("-") and out is None:
            out = arg
            i += 1
            continue
        passthrough.append(arg)
        i += 1

    if out is None:
        out = str(_ROOT / "dist" / "dotagents.pyz")

    from dotagents.cli.build_pyz import BuildPyz

    cmd = BuildPyz()
    cmd.out = Path(out)
    # Embed the running interpreter as the shebang, and (inside build-pyz) vendor
    # with sys.executable -- so "executing python" drives the whole build.
    cmd.python = sys.executable
    # Apply any pass-through `--flag value` overrides onto the command instance.
    j = 0
    while j < len(passthrough):
        flag = passthrough[j]
        if flag.startswith("--") and j + 1 < len(passthrough):
            attr = flag[2:].replace("-", "_")
            if hasattr(cmd, attr):
                setattr(cmd, attr, type(getattr(cmd, attr))(passthrough[j + 1])
                        if getattr(cmd, attr) is not None else passthrough[j + 1])
                j += 2
                continue
        j += 1

    return cmd()


if __name__ == "__main__":
    raise SystemExit(main())
