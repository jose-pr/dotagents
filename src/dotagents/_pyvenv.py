"""A Python venv per store, layered like every other walk.

Each store gets ``<store>/.pyvenv/<X.Y.Z>-<os>-<arch>/``, made from the
interpreter running dotagents. ``.pyvenv/`` holds the agents' venvs only --
where skills, overlay helpers and setup scripts run; a project's own code
gets its venvs elsewhere (the python overlay's ``<root>/.venv/``). The user store's venv always holds
:data:`BASE_REQUIREMENTS` (duho, for every skill) and the ``requirements.txt``
of each overlay installed there. A project gets its own venv only when one of
its overlays ships a ``requirements.txt`` or a ``lib/``; otherwise a project
session uses the user venv.

The venvs layer through a ``.pth`` file in each venv's site-packages. It lists,
highest precedence first: the store's own overlay libs, then each lower store's
venv site-packages and libs (user, then system). A project venv thus sees the
project's requirements, then the user venv's packages, then the system's, with
every overlay lib in the same project > user > system order the rest of
dotagents uses, and dotagents itself last (overlay libs import it). A ``.pth``
applies to that venv's interpreter only, so no other Python changes.

Nothing installs into the system store; a venv an administrator made there is
used as a layer when it exists. ``AGENTS_NO_PYVENV=1`` turns all of this off.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

#: Installed into every user venv, so each skill can import duho. The range is
#: dotagents' own (pyproject.toml), checked by the test suite.
BASE_REQUIREMENTS = ("duho>=0.7.5,<0.8",)
PYVENV_DIR = ".pyvenv"
REQUIREMENTS_FILE = "requirements.txt"
#: Beside the venv's interpreter tree: what was installed and layered.
STATE_FILE = ".dotagents-venv.json"
PTH_FILE = "dotagents-layers.pth"
DISABLE_ENV = "AGENTS_NO_PYVENV"


class VenvError(SystemExit):
    """A venv could not be created, or its requirements not installed."""


def disabled(environ: "Optional[dict]" = None) -> bool:
    value = (os.environ if environ is None else environ).get(DISABLE_ENV, "")
    return value.strip().lower() not in ("", "0", "false", "no", "off")


def os_bucket() -> str:
    """``nt`` / ``posix``, with macOS as ``darwin`` (``os.name`` calls it posix)."""
    if sys.platform == "darwin":
        return "darwin"
    return "nt" if sys.platform == "win32" else "posix"


def triplet() -> str:
    """``<X.Y.Z>-<os>-<arch>`` of the running interpreter. The arch is the one
    it was built for (``sysconfig``), not the host's: under emulation
    ``platform.machine()`` names the host."""
    import sysconfig

    arch = sysconfig.get_platform().strip().lower().rsplit("-", 1)[-1] or "unknown"
    return "%d.%d.%d-%s-%s" % (sys.version_info[:3] + (os_bucket(), arch))


def venv_dir(store: "str | os.PathLike[str]") -> Path:
    """The running interpreter's venv in ``store``."""
    return Path(store) / PYVENV_DIR / triplet()


def venv_python(venv: Path) -> Path:
    if sys.platform == "win32":
        return venv / "Scripts" / "python.exe"
    return venv / "bin" / "python"


def site_packages(venv: Path) -> Path:
    if sys.platform == "win32":
        return venv / "Lib" / "site-packages"
    found = sorted((venv / "lib").glob("python*/site-packages"))
    return found[0] if found else venv / "lib" / ("python%d.%d" % sys.version_info[:2]) / "site-packages"


def read_state(venv: Path) -> dict:
    try:
        with open(str(venv / STATE_FILE), encoding="utf-8") as fh:
            state = json.load(fh)
        return state if isinstance(state, dict) else {}
    except (OSError, ValueError):
        return {}


def ensure_venv(target: Path, python: "Optional[Path]" = None, *, with_pip: "Optional[bool]" = None,
                dry_run: bool = False, logger=None) -> "Optional[Path]":
    """The interpreter of the venv at ``target``, created from ``python``
    (default: the running one) when missing. ``with_pip=None`` uses ensurepip
    when the interpreter has it and falls back to ``--without-pip``; ``False``
    always creates it without pip. ``None`` on a dry run that would create it."""
    vpy = venv_python(target)
    if vpy.is_file():
        return vpy
    _log(logger, "info", "pyvenv: creating %s", target)
    if dry_run:
        return None
    base = [str(python or sys.executable), "-m", "venv"]
    target.parent.mkdir(parents=True, exist_ok=True)
    if with_pip is not False:
        done = subprocess.run(base + [str(target)], capture_output=True, text=True)
        if done.returncode != 0:
            if with_pip:
                raise VenvError("error: creating %s failed: %s" % (target, _tail(done)))
            _log(logger, "warning", "pyvenv: %s; creating it without pip", _tail(done))
            with_pip = False
    if with_pip is False:
        done = subprocess.run(base + ["--clear", "--without-pip", str(target)],
                              capture_output=True, text=True)
        if done.returncode != 0:
            raise VenvError("error: creating %s failed: %s" % (target, _tail(done)))
    if not vpy.is_file():
        raise VenvError("error: creating %s reported success but %s is missing" % (target, vpy))
    return vpy


def pip_command(vpy: Path, *, cache_dir: Path, logger=None) -> "list[str]":
    """How to run pip for the venv whose interpreter is ``vpy``: its own pip
    when it has one, else the pinned wheel driving it through ``--python``."""
    if (site_packages(vpy.parent.parent) / "pip").is_dir():
        return [str(vpy), "-m", "pip"]
    from dotagents._pipwheel import pip_wheel

    wheel = pip_wheel(cache_dir, logger=logger)
    return [sys.executable, os.path.join(str(wheel), "pip"), "--python", str(vpy.absolute())]


def layers(scope) -> "list[tuple[str, Path, list[Path]]]":
    """``(level, store, libs)`` for each store in play, highest precedence first;
    ``libs`` are that store's overlay libs then its own ``lib``, highest first."""
    from dotagents._env import get_lib_paths

    owner = {_key(o.path): o.store for o in scope.overlays}
    by_store: "dict[str, list[Path]]" = {}
    for lib in get_lib_paths(scope):
        store = owner.get(_key(lib.parent), lib.parent)
        by_store.setdefault(_key(store), []).append(lib)
    out = []
    for store in reversed(scope.stores):
        out.append((scope.store_level(store), Path(store), list(reversed(by_store.get(_key(store), [])))))
    return out


def requirement_files(scope, store: Path) -> "list[Path]":
    """The ``requirements.txt`` of each overlay in play that is installed in ``store``."""
    return [
        o.path / REQUIREMENTS_FILE for o in scope.overlays
        if _key(o.store) == _key(store) and (o.path / REQUIREMENTS_FILE).is_file()
    ]


def wants_venv(scope, level: str, store: Path, libs: "list[Path]") -> bool:
    """The user store always has a venv (duho); a project's only when it has
    requirements or libs of its own; nothing installs into the system store."""
    if level == "user":
        return True
    if level == "system":
        return False
    return bool(libs or requirement_files(scope, store))


def sync_venvs(scope, *, dry_run: bool = False, logger=None) -> "list[Path]":
    """Bring the scope's venvs up to date: the user venv, then the project's
    when it wants one. Requirements are installed when they changed since the
    last run; the ``.pth`` layering is rewritten every time. Returns the venvs
    that exist (or would, on a dry run)."""
    if disabled():
        return []
    stack = layers(scope)
    done = []
    # Lowest store first, so a project venv layers over an up-to-date user venv.
    for index in range(len(stack) - 1, -1, -1):
        level, store, libs = stack[index]
        if not wants_venv(scope, level, store, libs):
            continue
        reqs = list(BASE_REQUIREMENTS) if level == "user" else []
        _sync_one(scope, store, reqs, requirement_files(scope, store), stack[index:],
                  dry_run=dry_run, logger=logger)
        done.append(venv_dir(store))
    return done


def _sync_one(scope, store: Path, reqs: "list[str]", files: "list[Path]", stack,
              *, dry_run: bool, logger) -> None:
    venv = venv_dir(store)
    digest = _digest(reqs, files)
    state = read_state(venv)
    vpy = ensure_venv(venv, dry_run=dry_run, logger=logger)
    if vpy is None:  # dry run, venv missing
        _log(logger, "info", "pyvenv: would install %s", _describe(reqs, files))
        return
    if state.get("digest") != digest and (reqs or files):
        _install(scope, vpy, reqs, files, dry_run=dry_run, logger=logger)
    if dry_run:
        return
    entries = _pth_entries(stack)
    pth = site_packages(venv) / PTH_FILE
    text = "".join(e + "\n" for e in entries)
    if _read(pth) != text:
        pth.parent.mkdir(parents=True, exist_ok=True)
        _write_atomic(pth, text)
    state = {"digest": digest, "requirements": reqs + [str(f) for f in files], "pth": entries}
    if read_state(venv) != state:
        _write_atomic(venv / STATE_FILE, json.dumps(state, indent=2) + "\n")


def runtime_entry() -> str:
    """Where this dotagents imports from: the ``.pyz`` (which also carries
    duho and pathapi), else the directory holding the ``dotagents`` package.
    Overlay libs import ``dotagents`` (``dotagents._fs``, ``dotagents.cli``)."""
    from dotagents._resources import pyz_archive

    return pyz_archive() or str(Path(__file__).resolve().parent.parent)


def _pth_entries(stack) -> "list[str]":
    """The own store's libs, then each lower store's venv site-packages and
    libs, then dotagents itself -- last, so every venv's own packages win."""
    entries: "list[str]" = []
    for position, (_level, store, libs) in enumerate(stack):
        if position:
            lower = venv_dir(store)
            if venv_python(lower).is_file():
                entries.append(str(site_packages(lower)))
        entries += [str(lib) for lib in libs]
    entries.append(runtime_entry())
    out: "list[str]" = []
    for entry in entries:
        if entry not in out:
            out.append(entry)
    return out


def _install(scope, vpy: Path, reqs: "list[str]", files: "list[Path]", *, dry_run: bool, logger) -> None:
    from dotagents._pipwheel import find_links

    cache = Path(scope.user_root) / ".cache" / "pip"
    links: "list[str]" = []
    for link in find_links(cache_dir=cache, logger=logger):
        links += ["--find-links", link]
    args = ["install", "--disable-pip-version-check", "--no-input", *links]
    for path in files:
        args += ["-r", str(path)]
    args += reqs
    _log(logger, "info", "pyvenv: installing %s into %s%s", _describe(reqs, files),
         vpy.parent.parent, " [dry-run]" if dry_run else "")
    if dry_run:
        return
    command = pip_command(vpy, cache_dir=cache, logger=logger) + args
    rc = subprocess.run(command).returncode
    if rc != 0:
        raise VenvError("error: pip could not install %s into %s (exit %d)"
                        % (_describe(reqs, files), vpy.parent.parent, rc))


def active_venv(scope, environ: "Optional[dict]" = None) -> "Optional[Path]":
    """The venv a session in ``scope`` runs: the highest store's that a sync
    completed (its :data:`STATE_FILE` exists) for the running interpreter, or
    None. A venv anything else put there -- by hand, or a sync that failed
    before installing -- is never an agents venv."""
    if disabled(environ):
        return None
    for store in reversed(scope.stores):
        venv = venv_dir(store)
        if venv_python(venv).is_file() and (venv / STATE_FILE).is_file():
            return venv
    return None


def is_pyvenv_python(path: str) -> bool:
    """Whether ``path`` is the interpreter of a ``.pyvenv`` venv this module
    makes (``<store>/.pyvenv/<triplet>/{Scripts,bin}/python*``)."""
    parents = Path(path).parents
    return len(parents) > 2 and parents[2].name == PYVENV_DIR


def _digest(reqs: "list[str]", files: "list[Path]") -> str:
    h = hashlib.sha256()
    for req in reqs:
        h.update(req.encode("utf-8") + b"\0")
    for path in files:
        h.update(str(path).encode("utf-8") + b"\0" + (_read(path) or "").encode("utf-8") + b"\0")
    return h.hexdigest()


def _describe(reqs: "list[str]", files: "list[Path]") -> str:
    parts = list(reqs) + ["-r %s" % f for f in files]
    return ", ".join(parts) or "nothing"


def _read(path: Path) -> "Optional[str]":
    try:
        with open(str(path), encoding="utf-8", newline="") as fh:
            return fh.read()
    except OSError:
        return None


def _write_atomic(path: Path, text: str) -> None:
    from dotagents._fs import write_text_lf

    write_text_lf(path, text, atomic=True)


def _key(path: "str | os.PathLike[str]") -> str:
    return os.path.normcase(os.path.abspath(str(path)))


def _tail(done: subprocess.CompletedProcess) -> str:
    text = (done.stderr or done.stdout or "").strip().splitlines()
    return text[-1] if text else "exit %d" % done.returncode


def _log(logger, level: str, message: str, *args) -> None:
    if logger is not None:
        getattr(logger, level)(message, *args)
