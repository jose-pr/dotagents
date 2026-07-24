#!/usr/bin/env python3
"""private-sync overlay setup (D65).

Runs after ``overlays add private-sync`` copies this overlay into
``<scope>/.agents/overlays/private-sync/``. The D65 runner invokes it under the
current interpreter (Windows-safe) with cwd = the installed overlay dir and these
env vars set:

  * ``DOTAGENTS_AGENTS_DIR``  -- the resolved store path (D58 configurable store)
  * ``DOTAGENTS_OVERLAY_DIR`` -- this overlay's installed dir

**Idempotent** (check-then-act): it writes a marker-delimited managed block into
``<store>/env.py`` that the ``dotagents env`` chain executes at the store (user)
level. The block:

  * prepends this overlay's ``lib/`` to ``PYTHONPATH`` (so ``_link`` imports
    directly -- the fast path for the ``link-project`` / ``sync-project``
    commands, and what makes the overlay's own tests importable),
  * exports a stable ``PRIVATE_SYNC_ROOT`` so hooks/scripts reference
    ``$PRIVATE_SYNC_ROOT/hooks`` without hardcoding the store path.

**Not required for the commands to work** (D85): ``cmds/link_project.py`` and
``cmds/sync_project.py`` load ``lib/_link.py`` **by path** if a plain ``import
_link`` fails, so a discovered command works even when this script never ran, the
env block is not active, or the store was relocated. This setup is an
optimization and a convenience for scripts, never a prerequisite.

Re-running replaces the block in place; nothing outside the markers is touched.
Never prints ``DOTAGENTS_*``/``AGENTS_*`` values (Leakage) -- only derived paths.
"""
import os
import sys
from pathlib import Path

BEGIN = "# >>> dotagents:private-sync:begin >>>"
END = "# <<< dotagents:private-sync:end <<<"

# The managed block executed by `dotagents env`. It resolves the overlay root
# from the env dotagents passes at run time (falling back to the path baked in at
# setup), so a relocated store still works. It prints ONLY changed vars as JSON --
# the env.py contract (get_env_from_py).
BLOCK_TEMPLATE = '''\
{begin}
# Managed by 'overlays add private-sync' setup -- edits between the markers are overwritten.
import json as _ps_json, os as _ps_os
_ps_root = _ps_os.environ.get("PRIVATE_SYNC_ROOT") or {baked!r}
_ps_lib = _ps_os.path.join(_ps_root, "lib")
_ps_out = {{"PRIVATE_SYNC_ROOT": _ps_root}}
_ps_pp = _ps_os.environ.get("PYTHONPATH", "")
_ps_pp_parts = _ps_pp.split(_ps_os.pathsep) if _ps_pp else []
if _ps_lib not in _ps_pp_parts:
    _ps_out["PYTHONPATH"] = (_ps_lib + _ps_os.pathsep + _ps_pp) if _ps_pp else _ps_lib
print(_ps_json.dumps(_ps_out))
{end}
'''


def _strip_block(text):
    """Return ``text`` with any existing private-sync managed block removed."""
    lines = text.splitlines(keepends=True)
    out, skip = [], False
    for line in lines:
        stripped = line.rstrip("\r\n")
        if stripped == BEGIN:
            skip = True
            continue
        if stripped == END:
            skip = False
            continue
        if not skip:
            out.append(line)
    return "".join(out)


def main():
    agents_dir = os.environ.get("DOTAGENTS_AGENTS_DIR")
    if not agents_dir:
        sys.stderr.write(
            "private-sync setup: DOTAGENTS_AGENTS_DIR not set; refusing to guess store\n"
        )
        return 1
    overlay_dir = os.environ.get("DOTAGENTS_OVERLAY_DIR") or str(
        Path(__file__).resolve().parent
    )

    env_py = Path(agents_dir) / "env.py"
    block = BLOCK_TEMPLATE.format(begin=BEGIN, end=END, baked=str(overlay_dir))

    if env_py.is_file():
        existing = env_py.read_text(encoding="utf-8")
        base = _strip_block(existing)
        if base and not base.endswith("\n"):
            base += "\n"
    else:
        base = (
            "#!/usr/bin/env python3\n"
            "# dotagents store env.py (generated; hosts overlay-managed blocks).\n"
        )

    env_py.write_text(base + block, encoding="utf-8")
    # Path is safe to print (not a secret); the value of DOTAGENTS_* is never echoed.
    print("private-sync: wired lib/ (PYTHONPATH), PRIVATE_SYNC_ROOT -> %s" % env_py)
    return 0


if __name__ == "__main__":
    sys.exit(main())
