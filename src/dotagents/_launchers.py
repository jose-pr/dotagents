"""Launchers for overlays' scripts: each top-level ``bin/<name>.py``, plus
what ``overlay.toml`` declares (``Overlay.scripts``)::

    [scripts]
    tool = "lib/tool/main.py"   # another path, or another name
    helper = false              # no launcher for bin/helper.py

Each becomes an executable named after the key in ``<venv>/overlays/`` of a
dotagents venv (:mod:`dotagents._pyvenv`), which ``env`` puts first on
``PATH`` for the venv ``AGENTS_PYTHON`` names. A launcher runs the script
under that venv's interpreter, found at RUN time through the overlay's
``<NAME>_OVERLAY_ROOT``, so moving a store or syncing an overlay never
strands it; only a new interpreter needs a new launcher. With the variable
unset or the script missing it exits 2, saying so.

* Windows: ``<name>.exe``, made by distlib's ``ScriptMaker`` (pip's console
  script launcher) around a short Python bootstrap. A native program: no
  command interpreter parses its arguments, as cmd.exe would a ``.cmd``'s.
* POSIX: ``<name>``, a ``/bin/sh`` script that ``exec``s the interpreter.

:data:`MANIFEST` in the directory records which overlay made which launcher,
so one no overlay declares any more is deleted; files it does not list are
left alone. Writes are atomic and happen only when the bytes differ.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Optional

#: ``<venv>/overlays``: the launchers, and nothing of the venv's own.
DIR = "overlays"
MANIFEST = ".dotagents-launchers.json"
#: The time stamped on the bootstrap in an ``.exe``'s archive (1980-01-01, the
#: earliest a ZIP can hold): fixed, so equal inputs give equal bytes.
_EPOCH = "315532800"


class LauncherError(SystemExit):
    """Two overlays declare the same script name."""


def launcher_dir(venv: "str | os.PathLike[str]") -> Path:
    return Path(venv) / DIR


def is_launcher_dir(path: str) -> bool:
    """Whether ``path`` has the shape of any dotagents venv's launcher dir
    (``<store>/.pyvenv/<triplet>/overlays``); the filesystem is not consulted."""
    from dotagents._pyvenv import PYVENV_DIR

    parts = Path(os.path.normpath(path)).parts
    fold = (lambda s: s.lower()) if os.name == "nt" else (lambda s: s)
    return len(parts) >= 3 and fold(parts[-1]) == fold(DIR) and fold(parts[-3]) == fold(PYVENV_DIR)


def file_name(name: str) -> str:
    return name + ".exe" if os.name == "nt" else name


def declared(overlays) -> "dict[str, tuple]":
    """``{name: (overlay, script)}`` over ``overlays``; a name two overlays
    declare is a :class:`LauncherError` naming both."""
    out: "dict[str, tuple]" = {}
    seen: "dict[str, str]" = {}
    for overlay in overlays:
        for name, script in overlay.scripts().items():
            key = name.lower() if os.name == "nt" else name
            if key in seen and out[seen[key]][0].path != overlay.path:
                raise LauncherError(
                    "error: overlays %s and %s both declare the script %r"
                    % (out[seen[key]][0].name, overlay.name, name)
                )
            seen[key] = name
            out[name] = (overlay, script)
    return out


def _bootstrap(name: str, var: str, script: str) -> str:
    """The Python an ``.exe`` runs: the script as ``__main__``, as
    ``python <script>`` would."""
    return "\n".join([
        "#!python",
        "import os",
        "import runpy",
        "import sys",
        "",
        "root = os.environ.get(%r, '')" % var,
        "script = os.path.join(root, *%r)" % (script.split("/"),),
        "if not root or not os.path.isfile(script):",
        "    sys.stderr.write(%r %% root)" % (
            "%s: %s=%%s has no %s: run it in a `dotagents env` session\n" % (name, var, script)),
        "    sys.exit(2)",
        "sys.argv[0] = script",
        "sys.path[0] = os.path.dirname(script)",
        "runpy.run_path(script, run_name='__main__')",
        "",
    ])


def _sh_quote(text: str) -> str:
    return "'%s'" % text.replace("'", "'\\''")


def _shell(name: str, var: str, script: str, python: str) -> bytes:
    message = "%s: %s=${%s:-} has no %s: run it in a dotagents env session" % (name, var, var, script)
    path = '"$%s"%s' % (var, _sh_quote("/" + script))
    return "\n".join([
        "#!/bin/sh",
        "# Made by dotagents from overlay.toml [scripts]; regenerated on overlays sync.",
        'if [ -z "${%s:-}" ] || [ ! -f %s ]; then' % (var, path),
        '    echo "%s" >&2' % message,
        "    exit 2",
        "fi",
        "exec %s %s \"$@\"" % (_sh_quote(python), path),
        "",
    ]).encode("utf-8")


def _exe(name: str, var: str, script: str, python: str, work: str) -> bytes:
    from distlib.scripts import ScriptMaker

    source = os.path.join(work, "source")
    out = os.path.join(work, "out")
    os.mkdir(source)
    os.mkdir(out)
    with open(os.path.join(source, name + ".py"), "w", encoding="utf-8", newline="\n") as handle:
        handle.write(_bootstrap(name, var, script))
    maker = ScriptMaker(source, out, add_launchers=True)
    maker.executable = python
    maker.variants = {""}
    saved = os.environ.get("SOURCE_DATE_EPOCH")
    os.environ["SOURCE_DATE_EPOCH"] = _EPOCH
    try:
        maker.make(name + ".py")
    finally:
        if saved is None:
            del os.environ["SOURCE_DATE_EPOCH"]
        else:
            os.environ["SOURCE_DATE_EPOCH"] = saved
    with open(os.path.join(out, name + ".exe"), "rb") as handle:
        return handle.read()


def render(name: str, var: str, script: str, python: str) -> bytes:
    """The bytes of launcher ``name`` for this platform."""
    if os.name != "nt":
        return _shell(name, var, script, python)
    work = tempfile.mkdtemp(prefix="dotagents-launcher-")
    try:
        return _exe(name, var, script, python, work)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _read(path: Path) -> "Optional[bytes]":
    try:
        with open(str(path), "rb") as handle:
            return handle.read()
    except OSError:
        return None


def _write(path: Path, data: bytes, mode: "Optional[int]") -> None:
    partial = path.with_name(".%s.new" % path.name)
    with open(str(partial), "wb") as handle:
        handle.write(data)
    if mode is not None:
        os.chmod(str(partial), mode)
    try:
        os.replace(str(partial), str(path))
    except OSError:
        os.unlink(str(partial))
        raise


def sync(venv: Path, python: Path, overlays, *, dry_run: bool = False, logger=None) -> "list[str]":
    """Make ``<venv>/overlays`` hold exactly the launchers ``overlays``
    declare (interpreter ``python``), deleting those :data:`MANIFEST` lists
    that no overlay declares any more. Returns the launcher names written or
    deleted. A name declared twice is a :class:`LauncherError`, before any
    write."""
    wanted = declared(overlays)
    target = launcher_dir(venv)
    manifest_path = target / MANIFEST
    try:
        recorded = json.loads((_read(manifest_path) or b"{}").decode("utf-8"))
    except ValueError:
        recorded = {}
    if not isinstance(recorded, dict):
        recorded = {}
    changed: "list[str]" = []
    for name in sorted(set(recorded) - set(wanted)):
        path = target / file_name(name)
        if path.exists():
            _log(logger, "info", "launcher %s: removed (no overlay declares it)%s", path, _dry(dry_run))
            if not dry_run:
                path.unlink()
        changed.append(name)
    for name, (overlay, script) in sorted(wanted.items()):
        if not (overlay.path / script).is_file():
            _log(logger, "warning", "overlay %s: script %s (%s) not found; its launcher exits 2 until it is",
                 overlay.name, name, script)
        data = render(name, overlay.root_var, script, str(python))
        path = target / file_name(name)
        if _read(path) == data:
            continue
        _log(logger, "info", "launcher %s: %s (%s)%s", path, "updated" if path.exists() else "created",
             overlay.name, _dry(dry_run))
        changed.append(name)
        if not dry_run:
            target.mkdir(parents=True, exist_ok=True)
            _write(path, data, None if os.name == "nt" else 0o755)
    record = {name: overlay.name for name, (overlay, _script) in wanted.items()}
    if not dry_run and record != recorded:
        if record:
            target.mkdir(parents=True, exist_ok=True)
            _write(manifest_path, (json.dumps(record, indent=1, sort_keys=True) + "\n").encode("utf-8"), None)
        elif manifest_path.exists():
            manifest_path.unlink()
            try:
                target.rmdir()
            except OSError:
                pass  # something else lives there: left alone
    return changed


def _dry(dry_run: bool) -> str:
    return " [dry-run]" if dry_run else ""


def _log(logger, level: str, message: str, *args) -> None:
    if logger is not None:
        getattr(logger, level)(message, *args)
    elif level == "warning":
        sys.stderr.write("warning: " + (message % args) + "\n")
