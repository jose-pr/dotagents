#!/usr/bin/env python3
"""net overlay setup (D65).

Runs after ``overlays add net`` copies this overlay into
``<scope>/.agents/overlays/net/``. The D65 runner invokes it under the current
interpreter (Windows-safe) with cwd = the installed overlay dir and these env
vars set:

  * ``DOTAGENTS_AGENTS_DIR``  -- the resolved store path (D58 configurable store)
  * ``DOTAGENTS_OVERLAY_DIR`` -- this overlay's installed dir

**Idempotent** (check-then-act): it writes a marker-delimited managed block into
``<store>/env.py`` that the ``dotagents env`` chain executes at the store (user)
level. The block:

  * prepends this overlay's ``bin/`` to ``PATH`` (so the ``curl`` shim is found),
  * prepends its ``lib/`` to ``PYTHONPATH`` (so ``certifi``/``httplib`` import),
  * exports a stable ``NET_ROOT`` / ``NET_OVERLAY_ROOT`` (plan 03 ADAPT-narrow) so
    skills reference ``$NET_ROOT/lib`` without hardcoding the store path.

Re-running replaces the block in place; nothing outside the markers is touched.
Never prints ``DOTAGENTS_*``/``AGENTS_*`` values (Leakage) -- only derived paths.
"""
import os
import sys
from pathlib import Path

BEGIN = "# >>> dotagents:net:begin >>>"
END = "# <<< dotagents:net:end <<<"

# The managed block executed by `dotagents env`. It resolves NET_ROOT from the
# env dotagents passes at run time (falling back to the path baked in at setup),
# so a relocated store still works. It prints ONLY changed vars as JSON -- the
# env.py contract (get_env_from_py).
BLOCK_TEMPLATE = '''\
{begin}
# Managed by 'overlays add net' setup -- edits between the markers are overwritten.
import json as _net_json, os as _net_os
_net_root = _net_os.environ.get("NET_ROOT") or {baked!r}
_net_bin = _net_os.path.join(_net_root, "bin")
_net_lib = _net_os.path.join(_net_root, "lib")
_net_out = {{"NET_ROOT": _net_root, "NET_OVERLAY_ROOT": _net_root}}
_net_path = _net_os.environ.get("PATH", "")
_net_parts = _net_path.split(_net_os.pathsep) if _net_path else []
if _net_bin not in _net_parts:
    _net_out["PATH"] = (_net_bin + _net_os.pathsep + _net_path) if _net_path else _net_bin
_net_pp = _net_os.environ.get("PYTHONPATH", "")
_net_pp_parts = _net_pp.split(_net_os.pathsep) if _net_pp else []
if _net_lib not in _net_pp_parts:
    _net_out["PYTHONPATH"] = (_net_lib + _net_os.pathsep + _net_pp) if _net_pp else _net_lib
print(_net_json.dumps(_net_out))
{end}
'''


def _strip_block(text):
    """Return ``text`` with any existing net managed block removed."""
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
        sys.stderr.write("net setup: DOTAGENTS_AGENTS_DIR not set; refusing to guess store\n")
        return 1
    overlay_dir = os.environ.get("DOTAGENTS_OVERLAY_DIR") or str(Path(__file__).resolve().parent)

    env_py = Path(agents_dir) / "env.py"
    block = BLOCK_TEMPLATE.format(begin=BEGIN, end=END, baked=str(overlay_dir))

    if env_py.is_file():
        existing = env_py.read_text(encoding="utf-8")
        base = _strip_block(existing)
        if base and not base.endswith("\n"):
            base += "\n"
    else:
        base = "#!/usr/bin/env python3\n# dotagents store env.py (generated; hosts overlay-managed blocks).\n"

    env_py.write_text(base + block, encoding="utf-8")
    # Path is safe to print (not a secret); the value of DOTAGENTS_* is never echoed.
    print("net: wired bin/ (PATH), lib/ (PYTHONPATH), NET_ROOT -> %s" % env_py)
    return 0


if __name__ == "__main__":
    sys.exit(main())
