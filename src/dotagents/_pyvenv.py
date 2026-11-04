"""A Python venv per store, layered like every other walk.

Each store gets ``<store>/.pyvenv/<X.Y.Z>-<os>-<arch>/``, made from the
interpreter running dotagents. A store's ``.pyvenv/`` holds the agents' venvs
only -- where skills, overlay helpers and setup scripts run; a project's own
code gets its venvs beside the store, in the project root's ``.pyvenv/`` (the
python overlay's ``dotagents pyvenv``). The user store's venv always holds
:data:`BASE_REQUIREMENTS` (duho, for every skill). A project gets its own venv
only when one of its overlays has Python dependencies, a ``PYTHONPATH``
directory or a script; otherwise a project session uses the user venv.

Overlays' Python dependencies are not installed here but in their
environments (:mod:`dotagents._deps`), built under ``<venv>/envs/``; the
store's shared environment is the one this venv layers in.

The venvs layer through a ``.pth`` file in each venv's site-packages. It lists,
highest precedence first: the store's own overlay libs and its shared Python
environment, then each lower store's venv site-packages, libs and shared
environment (user, then system); an overlay's libs are the directories it
declares on ``PYTHONPATH``. A project venv thus sees the project's packages,
then the user's, then the system's, with every overlay lib in the same
project > user > system order the rest of dotagents uses, and dotagents
itself last (overlay libs import it). A ``.pth`` applies to that venv's
interpreter only, so no other Python changes.

Each venv also holds the launchers of the scripts its store's overlays and
every lower store's declare (``scripts``), in ``<venv>/overlays/``
(:mod:`dotagents._launchers`), brought up to date on every sync.

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

from dotagents import _launchers

#: Installed into every user venv, so each skill can import duho. The range is
#: dotagents' own (pyproject.toml), checked by the test suite.
BASE_REQUIREMENTS = ("duho>=0.7.9,<0.8",)
PYVENV_DIR = ".pyvenv"
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
    ``libs`` are that store's overlays' declared ``PYTHONPATH`` directories
    then its own ``lib``, highest first."""
    from dotagents._env import declared_paths

    out = []
    for store in reversed(scope.stores):
        libs: "list[Path]" = []
        for overlay in store_overlays(scope, [store]):
            libs += [p for p in declared_paths(overlay, "PYTHONPATH") if p.is_dir()]
        if (Path(store) / "lib").is_dir():
            libs.append(Path(store) / "lib")
        out.append((scope.store_level(store), Path(store), list(reversed(libs))))
    return out


def check_dependencies(path: Path, locked: bool, lock_digest: "Optional[str]") -> "list[str]":
    """Why a requirements or lock file may not be installed; empty when it may:
    only requirement lines (and, in a lock, ``==`` pins with hashes), and a
    lock whose bytes are its ``lock_digest``."""
    from dotagents import _aos

    try:
        data = path.read_bytes()
    except OSError as exc:
        return ["%s cannot be read (%s)" % (path, exc)]
    if lock_digest is not None and not _aos.same_digest(_aos.sha256_digest(data), lock_digest):
        return ["%s is not its lock_digest %s" % (path, lock_digest)]
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return ["%s is not UTF-8" % path]
    return ["%s: %s" % (path, problem) for problem in _aos.check_requirements(text, locked=locked)]


def store_overlays(scope, stores) -> list:
    """The overlays in play installed in any of ``stores``."""
    keys = {_key(s) for s in stores}
    return [o for o in scope.overlays if o.store is not None and _key(o.store) in keys]


def wants_venv(scope, level: str, store: Path, libs: "list[Path]") -> bool:
    """The user store always has a venv (duho); a project's only when it has
    Python dependencies, libs or scripts of its own; nothing installs into
    the system store."""
    from dotagents import _deps

    if level == "user":
        return True
    if level == "system":
        return False
    overlays = store_overlays(scope, [store])
    return bool(libs or any(o.scripts() for o in overlays)
                or any(key[0] == "python" for o in overlays for key in _deps.keys_of(o)))


def sync_venvs(scope, *, dry_run: bool = False, logger=None) -> "list[Path]":
    """Bring the scope's venvs up to date: the user venv, then the project's
    when it wants one. Requirements are installed when they changed since the
    last run; the ``.pth`` layering is rewritten every time. Returns the venvs
    that exist (or would, on a dry run)."""
    if disabled():
        scripts = sorted(n for o in scope.overlays for n in o.scripts())
        if scripts:
            _log(logger, "info", "pyvenv: %s is set, so no launcher is made for %s",
                 DISABLE_ENV, ", ".join(scripts))
        return []
    stack = layers(scope)
    done = []
    # Lowest store first, so a project venv layers over an up-to-date user venv.
    for index in range(len(stack) - 1, -1, -1):
        level, store, libs = stack[index]
        if not wants_venv(scope, level, store, libs):
            venv = venv_dir(store)
            if not dry_run and venv_python(venv).is_file() and (venv / STATE_FILE).is_file():
                # A venv a sync made that is no longer wanted can still be the
                # active one (active_venv): keep its launchers current.
                _launchers.sync(venv, venv_python(venv), _stack_overlays(scope, stack[index:]), logger=logger)
            continue
        reqs = list(BASE_REQUIREMENTS) if level == "user" else []
        _sync_one(scope, store, reqs, stack[index:], dry_run=dry_run, logger=logger)
        done.append(venv_dir(store))
    return done


def _sync_one(scope, store: Path, reqs: "list[str]", stack, *, dry_run: bool, logger) -> None:
    venv = venv_dir(store)
    digest = _digest(reqs)
    state = read_state(venv)
    vpy = ensure_venv(venv, dry_run=dry_run, logger=logger)
    if vpy is None:  # dry run, venv missing
        _log(logger, "info", "pyvenv: would install %s", ", ".join(reqs) or "nothing")
        return
    if state.get("digest") != digest and reqs:
        _install(scope, vpy, reqs, dry_run=dry_run, logger=logger)
    if dry_run:
        return
    entries = _write_pth(venv, stack)
    _launchers.sync(venv, vpy, _stack_overlays(scope, stack), logger=logger)
    state = {"digest": digest, "requirements": reqs, "pth": entries}
    if read_state(venv) != state:
        _write_atomic(venv / STATE_FILE, json.dumps(state, indent=2) + "\n")


def _write_pth(venv: Path, stack) -> "list[str]":
    entries = _pth_entries(stack)
    pth = site_packages(venv) / PTH_FILE
    text = "".join(e + "\n" for e in entries)
    if _read(pth) != text:
        pth.parent.mkdir(parents=True, exist_ok=True)
        _write_atomic(pth, text)
    return entries


def write_layers(scope) -> None:
    """Rewrite the ``.pth`` layering of each venv a sync made in ``scope``,
    so it names the shared Python environments' current builds."""
    if disabled():
        return
    stack = layers(scope)
    for index, (_level, store, _libs) in enumerate(stack):
        venv = venv_dir(store)
        if venv_python(venv).is_file() and (venv / STATE_FILE).is_file():
            entries = _write_pth(venv, stack[index:])
            state = read_state(venv)
            if state.get("pth") != entries:
                state["pth"] = entries
                _write_atomic(venv / STATE_FILE, json.dumps(state, indent=2) + "\n")


def _stack_overlays(scope, stack) -> list:
    """The overlays of the stores in ``stack`` (one venv's and every lower
    store's): what that venv's launchers serve."""
    return store_overlays(scope, [store for _level, store, _libs in stack])


def runtime_entry() -> str:
    """Where this dotagents imports from: the ``.pyz`` (which also carries
    duho and pathapi), else the directory holding the ``dotagents`` package.
    Overlay libs import ``dotagents`` (``dotagents._fs``, ``dotagents.cli``)."""
    from dotagents._resources import pyz_archive

    return pyz_archive() or str(Path(__file__).resolve().parent.parent)


def _pth_entries(stack) -> "list[str]":
    """The own store's libs and shared Python environment, then each lower
    store's venv site-packages, libs and shared environment, then dotagents
    itself -- last, so every venv's own packages win."""
    from dotagents import _deps

    entries: "list[str]" = []
    for position, (_level, store, libs) in enumerate(stack):
        if position:
            lower = venv_dir(store)
            if venv_python(lower).is_file():
                entries.append(str(site_packages(lower)))
        entries += [str(lib) for lib in libs]
        shared = _deps.packages_dir(store, ("python", "shared", ""))
        if shared is not None:
            entries.append(str(shared))
    entries.append(runtime_entry())
    out: "list[str]" = []
    for entry in entries:
        if entry not in out:
            out.append(entry)
    return out


def _install(scope, vpy: Path, reqs: "list[str]", *, dry_run: bool, logger) -> None:
    """dotagents' own requirements, in one pip run."""
    from dotagents._pipwheel import find_links

    cache = Path(scope.user_root) / ".cache" / "pip"
    links: "list[str]" = []
    for link in find_links(cache_dir=cache, logger=logger):
        links += ["--find-links", link]
    _log(logger, "info", "pyvenv: installing %s into %s%s", ", ".join(reqs), vpy.parent.parent,
         " [dry-run]" if dry_run else "")
    if dry_run:
        return
    command = pip_command(vpy, cache_dir=cache, logger=logger) + [
        "install", "--disable-pip-version-check", "--no-input", *links] + reqs
    rc = subprocess.run(command).returncode
    if rc != 0:
        raise VenvError("error: pip could not install %s into %s (exit %d)" % (", ".join(reqs), vpy.parent.parent, rc))


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


def _digest(reqs: "list[str]") -> str:
    h = hashlib.sha256()
    for req in reqs:
        h.update(req.encode("utf-8") + b"\0")
    return h.hexdigest()


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
