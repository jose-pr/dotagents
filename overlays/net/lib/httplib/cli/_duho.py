"""duho for the CLI: the interpreter's own when it has one (an installed
dotagents runs the launchers under ``AGENTS_PYTHON``, which has it), else the
``.pyz`` dotagents runs from, named in ``AGENTS_PYLIB`` and imported by
zipimport. The option classes live in real files, so duho's source-reading
field introspection works either way -- which is also why no class here
inherits a field from duho's own presets (their source is inside the zip)."""
import os
import sys

PYLIB_VAR = 'AGENTS_PYLIB'


class DuhoMissing(ImportError):
    """No duho to build the CLI with."""


def _load():
    try:
        import duho
        return duho
    except ImportError:
        pass
    for entry in (os.environ.get(PYLIB_VAR) or '').split(os.pathsep):
        if entry and entry not in sys.path:
            sys.path.append(entry)
    try:
        import duho
        return duho
    except ImportError:
        raise DuhoMissing('the curl fallback (httplib.cli) needs duho: run it in a dotagents session '
                          '(which exports AGENTS_PYTHON / %s) or pip install duho' % PYLIB_VAR)


duho = _load()
NS = duho.NS
Arg = duho.Arg
