#!/usr/bin/env python3
"""private-sync overlay setup (D65).

Runs after ``overlays add private-sync`` copies this overlay into
``<scope>/.agents/overlays/private-sync/``. The D65 runner invokes it under the
current interpreter (Windows-safe) with cwd = the installed overlay dir and these
env vars set:

  * ``AGENTS_HOME``        -- the resolved store path (D58 configurable store)
  * ``AGENTS_OVERLAY_DIR`` -- this overlay's installed dir

(the deprecated ``DOTAGENTS_AGENTS_DIR`` / ``DOTAGENTS_OVERLAY_DIR`` are still
read as a fallback for one release.)

**What dotagents now does on its own, so this script no longer has to**:
``dotagents env`` prepends every installed overlay's existing ``lib/`` to
``PYTHONPATH`` and exports ``PRIVATE_SYNC_OVERLAY_ROOT`` (one
``<NAME>_OVERLAY_ROOT`` per overlay), before the env-file chain runs -- so
``import _link`` resolves in any env-applied shell with no setup at all.

**What this script still does** (idempotent, check-then-act): it writes a
marker-delimited managed block into ``<store>/env.py`` that exports the short
alias ``PRIVATE_SYNC_ROOT`` = ``$PRIVATE_SYNC_OVERLAY_ROOT`` (falling back to the
path baked in at setup), so hooks/scripts can reference
``$PRIVATE_SYNC_ROOT/hooks`` without hardcoding the store path. Re-running
replaces the block in place -- which is also how a store that still carries the
OLD block (PYTHONPATH wiring, now redundant) gets it swapped for this one.

**Not required for the commands to work** (D85): ``cmds/link_project.py`` and
``cmds/sync_project.py`` load ``lib/_link.py`` **by path** if a plain ``import
_link`` fails, so a discovered command works even when this script never ran, the
env block is not active, or the store was relocated.

Nothing outside the markers is touched. Never prints ``DOTAGENTS_*``/``AGENTS_*``
values (Leakage) -- only derived paths.
"""
import os
import sys
from pathlib import Path

BEGIN = "# >>> dotagents:private-sync:begin >>>"
END = "# <<< dotagents:private-sync:end <<<"

# The managed block executed by `dotagents env`. It prints ONLY changed vars as
# JSON -- the env.py contract (get_env_from_py). PYTHONPATH and
# PRIVATE_SYNC_OVERLAY_ROOT are dotagents' own job now; only the alias is set here.
BLOCK_TEMPLATE = '''\
{begin}
# Managed by 'overlays add private-sync' setup -- edits between the markers are overwritten.
import json as _ps_json, os as _ps_os
_ps_root = _ps_os.environ.get("PRIVATE_SYNC_OVERLAY_ROOT") or {baked!r}
_ps_out = {{}}
if _ps_os.environ.get("PRIVATE_SYNC_ROOT") != _ps_root:
    _ps_out["PRIVATE_SYNC_ROOT"] = _ps_root
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
    agents_dir = os.environ.get("AGENTS_HOME") or os.environ.get("DOTAGENTS_AGENTS_DIR")
    if not agents_dir:
        sys.stderr.write(
            "private-sync setup: AGENTS_HOME not set; refusing to guess store\n"
        )
        return 1
    overlay_dir = (
        os.environ.get("AGENTS_OVERLAY_DIR")
        or os.environ.get("DOTAGENTS_OVERLAY_DIR")
        or str(Path(__file__).resolve().parent)
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

    with open(env_py, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(base + block)
    # Path is safe to print (not a secret); the value of AGENTS_* is never echoed.
    print("private-sync: PRIVATE_SYNC_ROOT alias -> %s (PYTHONPATH/PRIVATE_SYNC_OVERLAY_ROOT come from dotagents env)" % env_py)
    return 0


if __name__ == "__main__":
    sys.exit(main())
