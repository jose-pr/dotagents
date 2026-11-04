"""Overlays' dependencies: the environments their packages are installed into.

An overlay's dependency files (:func:`dotagents._aos.dependencies_of`: by
default ``lib/python/requirements.txt`` and ``lib/node/package.json``) go into
an environment its ``runtime`` variant chooses with ``environment``:
``shared`` (the default), a name, or ``isolated``. Shared and named
environments are per store, isolated ones per overlay:

- Python: ``<store>/.pyvenv/<triplet>/envs/<kind>/<generation>/``, filled by
  ``pip install --target`` with the store venv's pip. The shared one is listed
  in the venv's ``.pth`` (:mod:`dotagents._pyvenv`), so ``AGENTS_PYTHON``
  imports it; a named or isolated one, and the shared one when venvs are
  off, is added to ``PYTHONPATH`` after the overlay's ``lib/python/``.
- Node.js: ``<store>/.nodeenv/<os>/<kind>/<generation>/node_modules``, filled
  by npm from copies of the ``package.json`` (and ``package-lock.json``) only,
  added to ``NODE_PATH`` after ``lib/node/``.

``<kind>`` is ``shared``, ``named/<name>`` or ``isolated/<identity>``. Every
build goes into a new generation directory; the ``current`` file beside them
names the one in use and is replaced only when a build succeeded, so a failed
build leaves the environment as it was. A build installs every member's file
at once, in composition order, and takes out what no member asks for any
more. Two members asking for one package at different versions conflict:
neither is installed or activated, and the report names the package.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Iterable, Optional

from dotagents import _aos

#: Beside an environment's generations: the name of the one in use.
CURRENT = "current"
#: Seconds a package manager may run for one build.
BUILD_TIMEOUT = 1800

#: ``(store, key) -> inputs`` of the builds this process made, so one command
#: touching an environment for several overlays builds it once.
_BUILT: "dict[tuple[str, tuple[str, str, str]], str]" = {}


class DependencyError(Exception):
    """A dependency file that cannot be installed, or a build that failed."""


def kind(key: "tuple[str, str, str]") -> "list[str]":
    """The path components of an environment under its runtime's root."""
    _runtime, name, owner = key
    if name == "shared":
        return ["shared"]
    if name == "isolated":
        return ["isolated", owner]
    return ["named", name]


def runtime_root(store: "str | os.PathLike[str]", runtime: str) -> Path:
    """Where a store keeps the environments of ``runtime`` for this OS and interpreter."""
    from dotagents import _pyvenv

    if runtime == "python":
        return _pyvenv.venv_dir(store) / "envs"
    return Path(store) / ".nodeenv" / _aos.current_os()


def env_dir(store: "str | os.PathLike[str]", key: "tuple[str, str, str]") -> Path:
    return runtime_root(store, key[0]).joinpath(*kind(key))


def packages_dir(store: "str | os.PathLike[str]", key: "tuple[str, str, str]") -> Optional[Path]:
    """The packages directory of an environment's current build, or None."""
    root = env_dir(store, key)
    try:
        generation = (root / CURRENT).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not generation or "/" in generation or "\\" in generation:
        return None
    built = root / generation
    if key[0] == "node":
        built = built / "node_modules"
    return built if built.is_dir() else None


def dependencies(overlay: Any) -> "list[dict[str, Any]]":
    """The overlay's dependency files on this OS (:func:`_aos.dependencies_of`).
    Raises :class:`dotagents._aos.Refused` (``partial-lock``)."""
    from dotagents import _activate

    def read(rel: str) -> "Optional[bytes]":
        try:
            return overlay.path.joinpath(*rel.split("/")).read_bytes()
        except OSError:
            return None

    return _aos.dependencies_of(overlay.document(), _activate.files(overlay), _aos.current_os(), read)


def packages_for(overlay: Any) -> "dict[str, str]":
    """Runtime to the packages directory a session adds after the overlay's
    ``lib/<runtime>/``: its named or isolated Python environment, and its
    Node.js environment. The shared Python environment is reached through
    the venv's ``.pth`` instead, unless venvs are turned off."""
    from dotagents import _pyvenv

    if overlay.store is None:
        return {}
    layered = not _pyvenv.disabled()
    try:
        found = dependencies(overlay)
    except _aos.Refused:
        return {}
    out = {}
    for dependency in found:
        key = _aos.environment_key(dependency, overlay.normalized_name)
        if key[0] == "python" and key[1] == "shared" and layered:
            continue
        built = packages_dir(overlay.store, key)
        if built is not None:
            out[dependency["runtime"]] = str(built)
    return out


def _text(overlay: Any, rel: str) -> "Optional[str]":
    try:
        return overlay.path.joinpath(*rel.split("/")).read_bytes().decode("utf-8", "replace")
    except OSError:
        return None


def members(overlays: "Iterable[Any]") -> "list[tuple[Any, dict[str, Any], Optional[str]]]":
    """``(overlay, dependency, text of its file)`` for ``overlays`` in
    composition order; an overlay whose files cannot be read as dependencies
    contributes nothing."""
    out = []
    for overlay in sorted(overlays, key=lambda o: o.sort_key):
        try:
            found = dependencies(overlay)
        except _aos.Refused:
            continue
        out += [(overlay, dependency, _text(overlay, dependency["file"])) for dependency in found]
    return out


def conflicts(overlays: "Iterable[Any]") -> "dict[str, str]":
    """Identity to why it is blocked: two overlays of one environment (in one
    store) asking for one package at different versions."""
    by_store: "dict[str, list[Any]]" = {}
    for overlay in overlays:
        if overlay.store is not None:
            by_store.setdefault(str(overlay.store), []).append(overlay)
    out: "dict[str, str]" = {}
    for group in by_store.values():
        found = members(group)
        result = _aos.environments([(o.normalized_name, d, t) for o, d, t in found])
        for conflict in result["conflicts"]:
            for identity in conflict["overlays"]:
                others = [o for o in conflict["overlays"] if o != identity]
                out.setdefault(identity, "it asks for %s at another version than %s in the %s:%s environment" % (
                    conflict["package"], ", ".join(others), conflict["runtime"], conflict["environment"]))
    return out


def store_members(scope: Any, store: Path, *, exclude: "Iterable[str]" = ()) -> list:
    """The overlays installed in ``store`` that may be activated but for
    their preparation (pins and store settings checked), less ``exclude``."""
    from dotagents import _activate

    skip = {_path_key(store)}
    gone = set(exclude)
    return [o for o in scope.installed_overlays
            if o.store is not None and _path_key(o.store) in skip and o.normalized_name not in gone
            and _activate.refusal(o, prepared=False) is None]


def _path_key(path: "str | os.PathLike[str]") -> str:
    return os.path.normcase(os.path.abspath(str(path)))


def rebuild(scope: Any, store: Path, key: "tuple[str, str, str]", *, exclude: "Iterable[str]" = (),
            dry_run: bool = False, logger=None) -> "list[str]":
    """Build the environment ``key`` of ``store`` again from the files of
    its members (less ``exclude`` and those in a conflict), replacing the
    current build only when the new one succeeded; an environment left with
    no member is deleted. Returns the identities a conflict blocks. Raises
    :class:`DependencyError`."""
    found = members(store_members(scope, store, exclude=exclude))
    result = _aos.environments([(o.normalized_name, d, t) for o, d, t in found])
    for conflict in result["conflicts"]:
        _log(logger, "error", "dependencies: %s ask for %s at different versions in %s:%s; neither is "
             "installed or activated (give one its own environment)", " and ".join(conflict["overlays"]),
             conflict["package"], conflict["runtime"], conflict["environment"])
    built = next((b for b in result["environments"] if b["key"] == key), None)
    root = env_dir(store, key)
    if built is None:
        if root.exists():
            _log(logger, "info", "dependencies: %s has no member left; deleting it%s", root,
                 " [dry-run]" if dry_run else "")
            if not dry_run:
                shutil.rmtree(str(root), ignore_errors=True)
                _BUILT.pop((str(store), key), None)
        return result["blocked"]
    chosen = [(o, d) for o, d, _t in found if o.normalized_name in built["overlays"]
              and _aos.environment_key(d, o.normalized_name) == key]
    inputs = _inputs(chosen)
    memo = (str(store), key)
    if _BUILT.get(memo) == inputs and packages_dir(store, key) is not None:
        return result["blocked"]
    if not built["locked"]:
        _log(logger, "warning", "dependencies: %s:%s is not pinned (unlocked-dependencies), for %s",
             key[0], key[1], ", ".join(built["overlays"]))
    _log(logger, "info", "dependencies: building %s:%s from %s into %s%s", key[0], key[1],
         ", ".join("%s %s" % (o.name, d["file"]) for o, d in chosen), root, " [dry-run]" if dry_run else "")
    if dry_run:
        return result["blocked"]
    generation = "g%x" % time.time_ns()
    target = root / generation
    target.mkdir(parents=True)
    try:
        if key[0] == "python":
            _build_python(scope, store, chosen, target, built["locked"], logger)
        else:
            _build_node(chosen, target, built["locked"], logger)
    except BaseException:
        shutil.rmtree(str(target), ignore_errors=True)
        raise
    from dotagents._fs import write_text_lf

    write_text_lf(root / CURRENT, generation + "\n", atomic=True)
    _BUILT[memo] = inputs
    for old in root.iterdir():
        if old.is_dir() and old.name != generation:
            # A build still in use (a running process on Windows) goes with a later one.
            shutil.rmtree(str(old), ignore_errors=True)
    return result["blocked"]


def keys_of(overlay: Any) -> "list[tuple[str, str, str]]":
    """The environments an overlay's dependency files go into."""
    try:
        return [_aos.environment_key(d, overlay.normalized_name) for d in dependencies(overlay)]
    except _aos.Refused:
        return []


def _inputs(chosen: "list[tuple[Any, dict[str, Any]]]") -> str:
    h = hashlib.sha256()
    for overlay, dependency in chosen:
        h.update(overlay.normalized_name.encode("utf-8") + b"\0")
        for rel in (dependency["file"], dependency["lock"]):
            if rel:
                try:
                    h.update(overlay.path.joinpath(*rel.split("/")).read_bytes())
                except OSError:
                    pass
            h.update(b"\0")
    return h.hexdigest()


_HASH = re.compile(r"\s--hash[=\s]\S+")


def _build_python(scope: Any, store: Path, chosen: "list[tuple[Any, dict[str, Any]]]", target: Path,
                  locked: bool, logger) -> None:
    """One pip run: every member's file, in hash-checking mode (dependencies
    not followed, binary wheels only) when every file is a lock; otherwise
    their requirements together with the hashes left out, since pip checks
    hashes for every requirement or none."""
    from dotagents import _pyvenv
    from dotagents._pipwheel import find_links

    cache = Path(scope.user_root) / ".cache" / "pip"
    if _pyvenv.disabled():
        pip = [sys.executable, "-m", "pip"]
    else:
        vpy = _pyvenv.ensure_venv(_pyvenv.venv_dir(store), logger=logger)
        pip = _pyvenv.pip_command(vpy, cache_dir=cache, logger=logger)
    lines: "list[str]" = []
    for overlay, dependency in chosen:
        path = overlay.path.joinpath(*dependency["file"].split("/"))
        variant = _aos.first_variant(overlay.document().get("runtime", {}).get("python", []),
                                     _aos.current_os()) or {}
        problems = _pyvenv.check_dependencies(path, dependency["lock"] is not None,
                                              variant.get("lock_digest") if "lock" in variant else None)
        if problems:
            raise DependencyError("refusing to install: %s" % "; ".join(problems))
        text = path.read_bytes().decode("utf-8")
        for line in re.sub(r"\\\r?\n", " ", text).splitlines():
            line = re.sub(r"(?:^|\s)#.*$", "", line).strip()
            if line:
                lines.append(line if locked else _HASH.sub("", " " + line).strip())
    combined = target.parent / (".requirements-%s.txt" % target.name)
    from dotagents._fs import write_text_lf

    write_text_lf(combined, "".join(line + "\n" for line in lines))
    args = ["install", "--disable-pip-version-check", "--no-input", "--target", str(target)]
    for link in find_links(cache_dir=cache, logger=logger):
        args += ["--find-links", link]
    if locked:
        args += ["--require-hashes", "--no-deps", "--only-binary=:all:"]
    args += ["-r", str(combined)]
    import subprocess

    try:
        rc = subprocess.run(pip + args).returncode
    finally:
        try:
            combined.unlink()
        except OSError:
            pass
    if rc != 0:
        raise DependencyError("pip could not install %s (exit %d)" % (
            ", ".join("%s %s" % (o.name, d["file"]) for o, d in chosen), rc))


def npm_command() -> "Optional[list[str]]":
    """How to run npm: on Windows ``node`` with npm's own ``npm-cli.js`` (npm
    there is a batch file, which dotagents never runs), elsewhere ``npm`` on
    ``PATH``; None when there is none."""
    if os.name != "nt":
        found = shutil.which("npm")
        return [found] if found else None
    node = shutil.which("node")
    if node is None:
        return None
    cli = Path(node).parent / "node_modules" / "npm" / "bin" / "npm-cli.js"
    return [node, str(cli)] if cli.is_file() else None


def _build_node(chosen: "list[tuple[Any, dict[str, Any]]]", target: Path, locked: bool, logger) -> None:
    """``npm ci`` from a lone member's ``package.json`` and lock, else
    ``npm install`` from one ``package.json`` holding every member's
    dependencies; from copies of those files alone, so no ``.npmrc`` of an
    overlay configures npm."""
    from dotagents import _run

    command = npm_command()
    if command is None:
        raise DependencyError("npm is not on PATH")
    if locked:
        overlay, dependency = chosen[0]
        shutil.copyfile(str(overlay.path.joinpath(*dependency["file"].split("/"))), str(target / "package.json"))
        shutil.copyfile(str(overlay.path.joinpath(*dependency["lock"].split("/"))),
                        str(target / "package-lock.json"))
        verb = "ci"
    else:
        merged: "dict[str, Any]" = {}
        for overlay, dependency in chosen:
            try:
                declared = json.loads(overlay.path.joinpath(*dependency["file"].split("/")).read_text(
                    encoding="utf-8")).get("dependencies", {})
            except (OSError, ValueError, AttributeError) as exc:
                raise DependencyError("%s %s cannot be read (%s)" % (overlay.name, dependency["file"], exc))
            if isinstance(declared, dict):
                merged.update(declared)
        manifest = {"name": "dotagents-environment", "private": True, "dependencies": merged}
        (target / "package.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        verb = "install"
    status, _out = _run.run_step(command + [verb, "--prefix", str(target), "--no-audit", "--no-fund"],
                                 cwd=str(target), env=dict(os.environ), timeout=BUILD_TIMEOUT)
    if status != 0:
        raise DependencyError("npm %s exited %d for %s" % (
            verb, status, ", ".join("%s %s" % (o.name, d["file"]) for o, d in chosen)))
    (target / "node_modules").mkdir(exist_ok=True)


def signature(scope: Any) -> "list[str]":
    """What an environment's current build depends on, for the env cache:
    each ``current`` file of the scope's stores."""
    out = []
    for store in scope.stores:
        for runtime in ("python", "node"):
            root = runtime_root(store, runtime)
            if root.is_dir():
                for path in sorted(root.rglob(CURRENT)):
                    try:
                        out.append("env %s %s" % (path, path.read_text(encoding="utf-8").strip()))
                    except OSError:
                        pass
    return out


def _log(logger, level: str, message: str, *args: object) -> None:
    if logger is not None:
        getattr(logger, level)(message, *args)
