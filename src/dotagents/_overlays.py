"""Overlay support: the :class:`Overlay` type, plus the multi-overlay recompose.

An overlay is an AOS overlay (:mod:`dotagents._aos`): a directory, optionally
carrying an ``overlay.cfg`` manifest, that installs as a unit into
``<store>/overlays/<name>/``, its members keeping their relative paths.
Everything that depends only on ONE overlay -- its name rules, its manifest,
its setup, its members, its digest, what it contributes to the managed
``AGENTS.md`` block -- is a method or property of :class:`Overlay`. Nothing an
overlay holds means anything by its file name alone except ``overlay.cfg`` and
``skills/<name>/SKILL.md``: its environment, search paths, setup, context,
dependencies and commands are what the manifest declares. Manifest keys
dotagents applies:

* ``routing`` -- lines appended to the core's "Load on demand" list.
* ``rules`` -- overlay-relative paths to markdown files whose ``- **...`` bullet
  blocks are appended to "Always-on rules".
* ``requires`` -- overlay names this one needs; ``overlays add`` installs them first.
* ``context``, ``skills``, ``harness`` -- read by ``context`` and the skills publisher.
* ``env``, ``env_files``, ``paths``, ``env_scripts`` -- applied by ``dotagents env``.
* ``runtime``, ``setup`` -- probed and run by ``overlays add`` / ``sync``.
* ``scripts`` -- launchers (:mod:`dotagents._launchers`).
* ``mcp`` -- MCP servers and the skills they back; read by ``dotagents._mcp_gateway_config``.
* ``name``, ``description``, ``priority`` -- see :meth:`Overlay.read_manifest`.

A manifest that cannot be read or does not validate contributes nothing at
all, with a warning naming where it is wrong.

An installed overlay's install record and state live beside it, never in it:
``<store>/overlays/.dotagents-install/<name>.json`` and
``<store>/overlays/.dotagents-state/<name>/<os>-<digest>/``, so the installed
copy holds exactly the overlay's members and keeps its tree digest.

The operations over a *set* of overlays stay module functions:
``_compose_block`` folds their rules and routing into a base block, and
:func:`recompose_overlay_block` rewrites a store's ``AGENTS.md`` with it.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable, Optional

from dotagents import _aos

_log = logging.getLogger("dotagents")
_warned: "set[str]" = set()


def _warn_once(message: str, *args: object) -> None:
    """Log a warning once per process: manifests are re-read many times per
    command (priority, sort key, compose), and one problem is one line."""
    text = message % args if args else message
    if text not in _warned:
        _warned.add(text)
        _log.warning("%s", text)


# Default merge priority for an overlay that declares none. Lower sorts
# earlier. 500 leaves generous headroom on both sides for overlays that want to
# sort before (0..499) or after (501..) the unprioritized default.
DEFAULT_PRIORITY = 500

#: Seconds a setup variant may run when it declares no ``timeout``.
DEFAULT_SETUP_TIMEOUT = 300


# --------------------------------------------------------------------------- #
# Reading the manifest: ``overlay.cfg`` through :mod:`dotagents._aos`.
# --------------------------------------------------------------------------- #

#: ``str(path) -> (stat signature, document, problems)``: a manifest is read
#: once per change, however often a command asks for it.
_MANIFESTS: "dict[str, tuple[tuple[int, int], dict[str, Any], list[str]]]" = {}


def load_manifest(path: Path) -> "tuple[dict[str, Any], list[str]]":
    """``(document, problems)`` of the manifest at ``path``: the validated
    data model, or ``{}`` with the reasons it was refused. An absent file is
    ``({}, [])``: an overlay need not have a manifest."""
    try:
        st = os.stat(str(path))
    except OSError:
        return {}, []
    key = str(path)
    sig = (st.st_mtime_ns, st.st_size)
    cached = _MANIFESTS.get(key)
    if cached is not None and cached[0] == sig:
        return cached[1], cached[2]
    try:
        with open(key, "rb") as fh:
            data = fh.read()
        doc = _aos.read_document(data)
        problems = ["%s at %s" % (code, at or "/") for code, at in _aos.validate(doc)]
    except _aos.ReadError as exc:
        doc, problems = {}, ["cannot be read (%s)" % exc]
    except OSError as exc:
        doc, problems = {}, ["cannot be read (%s)" % exc]
    if problems:
        doc = {}
    _MANIFESTS[key] = (sig, doc, problems)
    return doc, problems


def _string_list(value: object) -> "list[str]":
    """Routing lines with their surrounding newlines trimmed (a TOML
    multi-line string starts with one), blanks dropped."""
    if not isinstance(value, list):
        return []
    return [v.strip("\n") for v in value if isinstance(v, str) and v.strip()]


def _file_digest(path: Path) -> str:
    import hashlib

    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


class InstallResult(object):
    """What :meth:`Overlay.install_to` did. Unpacks as the historical
    ``(written, skipped, lines)``, ``skipped`` being the unchanged files."""

    def __init__(self) -> None:
        self.written = 0
        self.unchanged = 0
        #: Files replaced although they were not what dotagents installed
        #: (edited here): backed up first when a backup root was given.
        self.replaced: "list[str]" = []
        #: Files removed: not in the source, or cleared by ``prune``.
        self.removed: "list[str]" = []
        #: Files copied to the backup root before being replaced or removed.
        self.backed_up: "list[str]" = []
        self.lines: "list[str]" = []

    @property
    def skipped(self) -> int:
        return self.unchanged

    def __iter__(self):
        return iter((self.written, self.skipped, self.lines))


def _same_content(a: Path, b: Path) -> bool:
    try:
        if a.stat().st_size != b.stat().st_size:
            return False
        return a.read_bytes() == b.read_bytes()
    except OSError:
        return False


# --------------------------------------------------------------------------- #
# The overlay itself.
# --------------------------------------------------------------------------- #


class Overlay:
    """One overlay: a directory whose NAME identifies it and whose files install
    as a unit under ``<scope>/overlays/<name>/``.

    Construct it from the overlay's directory (a source dir or an installed
    dir -- both are overlays). Nothing is read at construction; every method
    reads the directory when called, so an ``Overlay`` made before its files
    land still sees them afterwards. The name-only rules (:meth:`is_valid_name`,
    :meth:`normalize_name`, :meth:`root_var_for`) are static so a caller holding
    just a name -- ``overlays add <name>`` -- uses the same rule as one holding a
    directory.
    """

    #: A directory under ``overlays/`` is an overlay IFF its name matches this: a
    #: leading ASCII letter, then ASCII letters/digits/``_``/``.``/``-``, ending
    #: in a letter or digit. A dot is allowed MID-name (``foo.bar``, ``v1.2``)
    #: but not first, so ``.git``/``.hidden`` are excluded, nor last: Win32
    #: strips a trailing dot, so ``python.`` would BE ``python`` on disk. A
    #: leading underscore (``__pycache__``) and a leading digit (``2fast``) are
    #: excluded too. One shared rule for :meth:`discover` and ``overlays add``
    #: (D84). Whether a path IS a directory is checked separately with
    #: ``is_dir()``, which follows symlinks -- a symlink-to-dir is a valid overlay.
    NAME_RE = re.compile(r"^[A-Za-z](?:[A-Za-z0-9_.-]*[A-Za-z0-9])?$")

    #: Windows device names: ``con`` / ``nul`` / ``com1`` (with any extension)
    #: name the device, not a directory, on every Windows drive.
    RESERVED_NAMES = frozenset(
        ["con", "prn", "aux", "nul"]
        + ["com%d" % i for i in range(1, 10)]
        + ["lpt%d" % i for i in range(1, 10)]
    )

    #: The optional manifest an overlay may carry at its root.
    MANIFEST_NAME = "overlay.cfg"
    #: Not a manifest: an overlay that has only this gets a warning naming it.
    OLD_MANIFEST_NAME = "overlay.toml"

    DEFAULT_PRIORITY = DEFAULT_PRIORITY

    __slots__ = ("path", "store")

    def __init__(
        self, path: "str | os.PathLike[str]", store: "str | os.PathLike[str] | None" = None
    ):
        self.path = Path(path)
        #: The store (an ``.agents`` root) this overlay was discovered in, when it
        #: came from :meth:`installed`; ``None`` for a bare directory. Not part
        #: of identity (``__eq__`` / ``__hash__`` are the path only).
        self.store: "Optional[Path]" = Path(store) if store is not None else None

    # -- identity: the plain object protocol ---------------------------------

    def __repr__(self) -> str:
        return "Overlay(%r)" % str(self.path)

    def __fspath__(self) -> str:
        return str(self.path)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Overlay) and self.path == other.path

    def __hash__(self) -> int:
        return hash(self.path)

    # -- name rules (static: usable with only a name in hand) -----------------

    @staticmethod
    def is_valid_name(name: str) -> bool:
        """A dir under ``overlays/`` is an overlay iff its name matches
        :data:`NAME_RE`: a leading ASCII letter, then ASCII letters/digits/``_``/
        ``.``/``-``. Dots are allowed mid-name (``foo.bar``, ``v1.2``) but NOT as
        the first char, so ``.git``/``.hidden`` and ``__pycache__`` (leading ``_``)
        and ``2fast`` (leading digit) are excluded. The contract-A level names
        (``user``, ``project``, ``system``, ``project-root``, ``default``,
        ``overlay`` -- :data:`dotagents._scope.LEVEL_NAMES`) are reserved: an
        overlay's dir name is its level label in the walk, and one of these
        would collide with the per-level filename keys. So are Windows device
        names (:data:`RESERVED_NAMES`, with or without an extension)."""
        from dotagents._scope import LEVEL_NAMES

        return (
            bool(Overlay.NAME_RE.match(name))
            and name.lower() not in LEVEL_NAMES
            and name.split(".", 1)[0].lower() not in Overlay.RESERVED_NAMES
        )

    @staticmethod
    def normalize_name(name: str) -> str:
        """THE canonical overlay name: lowercase, ``_`` and ``.`` -> ``-``.

        Every place that refers to an overlay by name goes through this one
        function, so ``My_Overlay`` and ``my-overlay`` are one overlay everywhere:
        ``overlays add`` installs to ``<scope>/overlays/<normalize_name(name)>/``
        and resolves the source dir by the same name, shadowing across stores
        compares it, and :meth:`root_var_for` derives the ``<NAME>_OVERLAY_ROOT``
        env var from it. A dot is legal in a dir name (:meth:`is_valid_name`)
        but no shell variable can carry it, so ``v1.2`` and ``v1-2`` would share
        ``V1_2_OVERLAY_ROOT``; normalizing it to ``-`` makes them one overlay
        rather than two that alias."""
        return name.lower().replace("_", "-").replace(".", "-")

    @staticmethod
    def root_var_for(name: str) -> str:
        """The env-var name carrying an overlay's installed root: ``<NAME>_OVERLAY_ROOT``.

        ``NAME`` is :meth:`normalize_name` of the overlay upper-cased, with ``-``
        (and a mid-name ``.``, which no shell variable can carry) turned into
        ``_``: ``my-overlay`` / ``My_Overlay`` -> ``MY_OVERLAY_OVERLAY_ROOT``,
        ``v1.2`` -> ``V1_2_OVERLAY_ROOT``. Because it starts from the same
        normalized name the install dir uses, the variable for ``overlays/<n>/``
        is always ``root_var_for(n)`` (:attr:`root_var` on the instance). Both
        consumers go through here: ``dotagents env`` EMITS the variable and
        ``dotagents context`` expands the ``<NAME_OVERLAY_ROOT>`` placeholder to
        the same path."""
        return re.sub(r"[^A-Z0-9]", "_", Overlay.normalize_name(name).upper()) + "_OVERLAY_ROOT"

    # -- per-instance identity ------------------------------------------------

    @property
    def name(self) -> str:
        """The overlay's name: its directory name."""
        return self.path.name

    @property
    def normalized_name(self) -> str:
        """:meth:`normalize_name` of :attr:`name`."""
        return self.normalize_name(self.name)

    @property
    def root_var(self) -> str:
        """:meth:`root_var_for` of :attr:`name` -- the ``<NAME>_OVERLAY_ROOT`` var."""
        return self.root_var_for(self.name)

    @property
    def is_valid(self) -> bool:
        """:meth:`is_valid_name` of :attr:`name`."""
        return self.is_valid_name(self.name)

    @property
    def manifest_path(self) -> Path:
        return self.path / self.MANIFEST_NAME

    # -- discovery ------------------------------------------------------------

    @classmethod
    def discover(
        cls, root: "str | os.PathLike[str]", store: "str | os.PathLike[str] | None" = None
    ) -> "list[Overlay]":
        """The overlays under ONE ``overlays/`` root, sorted by name.

        No registry, no manifest required: a directory under ``root`` *is* an
        overlay as long as its name passes :meth:`is_valid_name` (so ``.git``/
        ``__pycache__``/dotfiles are skipped). ``is_dir()`` follows symlinks, so a
        symlink-to-dir counts. Empty if ``root`` is absent. This is the ONE
        discovery rule: :meth:`installed` folds it over several stores,
        and the ``overlays`` command's ``add``/``remove``/``sync`` use it directly
        for the single scope they install into. ``store`` is stamped on each
        result (see :attr:`store`)."""
        root = Path(root)
        if not root.is_dir():
            return []
        found = [
            cls(p, store)
            for p in sorted(root.iterdir())
            if p.is_dir() and cls.is_valid_name(p.name)
        ]
        seen: "dict[str, str]" = {}
        for overlay in found:
            other = seen.setdefault(overlay.normalized_name, overlay.name)
            if other != overlay.name:
                _warn_once(
                    "overlays %s and %s in %s are one overlay (%s) and share %s; remove one",
                    other, overlay.name, root, overlay.normalized_name, overlay.root_var,
                )
        return found

    @classmethod
    def installed(cls, *stores: "str | os.PathLike[str] | None") -> "list[Overlay]":
        """Every overlay a session uses, across ``stores`` (``.agents``-shaped
        roots) given in precedence order -- ``Scope.stores``: the system store,
        the user store, then the project's ``<project>/.agents`` (a user scope
        has no project store). A ``None`` store is skipped.

        The overlays come back store by store, each store's sorted by name,
        with every result stamped with the :attr:`store` it came from.
        **Shadowing:** an overlay whose name (compared normalized, so
        ``my_overlay`` and ``my-overlay`` are one) also appears in a LATER store
        is dropped -- the later store's copy REPLACES it, so the project's bin/lib/
        env/cmds/CONTEXT.md/root var are the only ones that resolve, not both
        stacked. This is the one function behind the contract-A walk
        (``Scope.paths``), ``env``'s overlay roots, ``context``'s
        sources/placeholders/skills and ``overlays list``/``show``, so they can
        never disagree about what is installed."""
        per_store = [
            cls.discover(Path(store) / "overlays", store)
            for store in stores
            if store is not None
        ]
        result: "list[Overlay]" = []
        for i, found in enumerate(per_store):
            later = {o.normalized_name for rest in per_store[i + 1 :] for o in rest}
            result.extend(o for o in found if o.normalized_name not in later)
        return result

    # -- manifest -------------------------------------------------------------

    def document(self) -> "dict[str, Any]":
        """The manifest's data model, validated (``{}`` when there is none, or
        when it cannot be read or validated: :meth:`manifest_problems` says why,
        and a warning names it once)."""
        doc, problems = load_manifest(self.manifest_path)
        if problems:
            _warn_once("overlay %s: %s is ignored, none of its declarations apply: %s",
                       self.name, self.manifest_path, "; ".join(problems))
        elif not doc and not self.manifest_path.exists() and (self.path / self.OLD_MANIFEST_NAME).is_file():
            _warn_once("overlay %s: %s is not read; its manifest is %s (AOS)",
                       self.name, self.OLD_MANIFEST_NAME, self.MANIFEST_NAME)
        return doc

    def manifest_problems(self) -> "list[str]":
        """Why the manifest was refused; empty when it is valid or absent."""
        return load_manifest(self.manifest_path)[1]

    def read_manifest(self) -> "dict[str, Any]":
        """The manifest's core fields as dotagents uses them: name (the
        directory name when absent or empty), description, routing (lines
        trimmed of surrounding newlines, blanks dropped), rules, requires
        (normalized, de-duplicated), priority (default ``DEFAULT_PRIORITY``),
        and ``doc``, the whole validated document.

        An overlay is allowed to be just a directory of files: a missing
        manifest, or one refused (:meth:`document`), yields the defaults."""
        doc = self.document()
        requires: "list[str]" = []
        for raw in doc.get("requires", []):
            dep = self.normalize_name(raw)
            if dep not in requires:
                requires.append(dep)
        name = doc.get("name")
        return {
            "name": name if name else self.name,
            "description": doc.get("description", ""),
            "routing": _string_list(doc.get("routing")),
            "rules": list(doc.get("rules", [])),
            "requires": requires,
            "priority": doc.get("priority", DEFAULT_PRIORITY),
            "doc": doc,
        }

    def skills(self) -> "list[str]":
        """The overlay's skills: its ``skills`` list, else every
        ``skills/<name>/`` holding a ``SKILL.md``."""
        doc = self.document()
        if "skills" in doc:
            return list(doc["skills"])
        skills_dir = self.path / "skills"
        if not skills_dir.is_dir():
            return []
        return sorted(d.name for d in skills_dir.iterdir() if (d / "SKILL.md").is_file())

    def context_files(self, harness_id: "Optional[str]" = None) -> "list[str]":
        """The files the overlay adds to an agent's initial context: its
        ``context`` (when absent, ``CONTEXT.md`` if the root has one), then
        ``harness.<harness_id>.context``."""
        doc = self.document()
        if "context" in doc:
            out = list(doc["context"])
        else:
            out = ["CONTEXT.md"] if (self.path / "CONTEXT.md").is_file() else []
        if harness_id:
            out += list(doc.get("harness", {}).get(harness_id, {}).get("context", []))
        return out

    def harness_hints(self, harness_id: str) -> "dict[str, Any]":
        """``harness.<harness_id>``: ``context``, ``skills``, ``model``,
        ``effort``, and that harness's own keys, which dotagents never applies."""
        return dict(self.document().get("harness", {}).get(harness_id, {}))

    def setup_variant(self, os_name: "Optional[str]" = None) -> "Optional[dict[str, Any]]":
        """The ``setup`` variant that applies on ``os_name`` (this OS); with no
        ``setup`` declared, the default ``setup.<ext>`` at the root, the first
        this OS can run (:func:`dotagents._aos.default_script`); else None."""
        doc = self.document()
        if "setup" in doc:
            return _aos.first_variant(doc["setup"], os_name or _aos.current_os())
        return self.default_setup()[0]

    def default_setup(self) -> "tuple[Optional[dict[str, Any]], list[str]]":
        """``(variant, skipped)``: the default ``setup.<ext>`` that runs here, and
        those present that cannot (``[defaults] setup = false``: none)."""
        from dotagents._run import can_run

        return _aos.default_script(self.document(), "setup", lambda rel: (self.path / rel).is_file(), can_run)

    def declared_scripts(self, os_name: "Optional[str]" = None) -> "dict[str, tuple[str, str]]":
        """The overlay's commands on ``os_name`` (this OS), as ``{name: (path,
        interpreter)}``: by default each top-level ``bin/<name>.py`` (unless
        ``[defaults] bin = false``), then its ``scripts``, which add, replace
        or, with ``false``, drop one by name."""
        os_name = os_name or _aos.current_os()
        doc = self.document()
        out: "dict[str, tuple[str, str]]" = {}
        bin_dir = self.path / "bin"
        if doc.get("defaults", {}).get("bin", True) and bin_dir.is_dir():
            for entry in sorted(bin_dir.iterdir()):
                stem = entry.name[:-3]
                if entry.name.endswith(".py") and entry.is_file() and _aos._SCRIPT_NAME.match(stem) \
                        and stem.split(".")[0].lower() not in self.RESERVED_NAMES:
                    out[stem] = ("bin/" + entry.name, "python")
        for name, target in doc.get("scripts", {}).items():
            if target is False:
                out.pop(name, None)
                continue
            if isinstance(target, dict):
                if _aos.os_matches(target, os_name):
                    out[name] = (target["path"], target["interpreter"])
            else:
                out[name] = (target, _aos.script_interpreter(target) or "")
        return out

    def scripts(self) -> "dict[str, str]":
        """The scripts that get a launcher (:mod:`dotagents._launchers`): the
        declared ones that run under ``python``. A launcher runs its script
        under the scope venv's Python, so a script naming another interpreter
        gets none (``overlays add`` refuses such an overlay)."""
        return {name: path for name, (path, interpreter) in self.declared_scripts().items()
                if interpreter == "python"}

    def unsupported_scripts(self) -> "dict[str, str]":
        """``{name: interpreter}`` of declared scripts dotagents cannot launch."""
        return {name: interpreter for name, (_path, interpreter) in self.declared_scripts().items()
                if interpreter != "python"}
    @property
    def priority(self) -> int:
        """The manifest ``priority``, ``DEFAULT_PRIORITY`` when absent or
        unparseable (:meth:`read_manifest` already did the checking). Lower
        sorts earlier."""
        return self.read_manifest()["priority"]  # type: ignore[return-value]

    @property
    def sort_key(self) -> "tuple[int, str, str]":
        """The `(priority, name)` merge-order key.

        Lower `priority` (default `DEFAULT_PRIORITY`, 500) sorts earlier; a
        numerically higher-priority overlay therefore lands *later* in the merged
        AGENTS.md block and wins on conflict -- the same convention `_context.py`
        uses. The tiebreaker is the manifest `name` (falling back to the directory
        name), then the directory name itself (two dirs can carry the same
        manifest name), so output is deterministic regardless of input order."""
        manifest = self.read_manifest()
        return (self.priority, str(manifest.get("name", self.name)), self.name)

    @classmethod
    def sort_by_priority(
        cls, overlays: "Iterable[Overlay | str | os.PathLike[str]]"
    ) -> "list[Overlay]":
        """`overlays` (instances or dirs) sorted by `(priority, name)`.

        Deterministic regardless of the caller's order (`add` passes invocation
        order, `sync` discovery order): a high-priority overlay's lines must
        always land last. See :attr:`sort_key`."""
        return sorted((cls(o) for o in overlays), key=lambda o: o.sort_key)

    # -- setup ---------------------------------------------------------------

    def run_setup(
        self, variant: "dict[str, Any]", *, env: "dict[str, str]", python: str,
        base_path: "Optional[str]", dry_run: bool, logger, timeout: "Optional[int]" = None,
    ) -> int:
        """Run one ``setup`` variant (:meth:`setup_variant`) and return its exit
        status, ``0`` on success.

        It runs as AOS says a step does: its interpreter (``python`` is the
        scope venv's, the others are found on ``base_path``, the base
        environment's ``PATH``) with the script's absolute path and its
        ``args`` as a list; the working directory the overlay root; ``env``
        (the caller builds it: the overlay's own environment and the
        ``AOS_OVERLAY_*`` variables); stopped after ``timeout`` (the
        command's override), else the variant's, else
        :data:`DEFAULT_SETUP_TIMEOUT` seconds (``0``: no limit), a stop being
        a failure, :data:`dotagents._run.TIMED_OUT`; and everything it
        started ends with it. An interpreter dotagents cannot run is a
        failure before anything runs (exit 127)."""
        from dotagents import _run

        limit = variant.get("timeout", DEFAULT_SETUP_TIMEOUT) if timeout is None else max(0, timeout)
        logger.info("running setup for %s (%s %s)", self.name, variant["interpreter"], variant["path"])
        try:
            argv = _run.interpreter_command(variant["interpreter"], python=python, base_path=base_path)
        except _run.InterpreterError as exc:
            logger.error("setup for %s: %s", self.name, exc)
            return 127
        if dry_run:
            logger.info("[dry-run] would run %s in %s", variant["path"], self.path)
            return 0
        script = _aos.resolve_path(str(self.path), variant["path"], os.name == "nt")
        try:
            status, _out = _run.run_step(argv + [script] + list(variant.get("args", [])),
                                         cwd=str(self.path), env=env, timeout=limit)
        except OSError as exc:
            logger.error("setup for %s failed to start: %s", self.name, exc)
            return 1
        if status == _run.TIMED_OUT:
            logger.error("setup for %s did not finish in %ds and was stopped (raise its timeout in "
                         "overlay.cfg or with --setup-timeout; 0 = no limit)", self.name, limit)
        elif status != 0:
            logger.error("setup for %s exited %d", self.name, status)
        return status

    # -- members, digest, state -----------------------------------------------

    #: Beside the store's overlays: one install record per overlay.
    RECORDS_DIR = ".dotagents-install"
    #: Beside the store's overlays: ``<name>/<os>-<digest>/`` state directories.
    STATE_DIR = ".dotagents-state"
    #: An install record kept inside the overlay: read when the record beside
    #: it is absent, and removed by the next install.
    OLD_INSTALL_RECORD = ".dotagents-install.json"

    def members(self) -> "list[str]":
        """The overlay's members, relative and sorted: every regular file
        except what AOS excludes. Raises :class:`dotagents._aos.DigestError`
        for a tree that holds a link, a special file or a name AOS refuses."""
        return _aos.members(self.path)

    def files(self) -> "list[Path]":
        """The members as paths (:meth:`members`)."""
        return [self.path.joinpath(*rel.split("/")) for rel in self.members()]

    def tree_digest(self) -> str:
        """The AOS tree digest of the overlay. Raises
        :class:`dotagents._aos.DigestError`."""
        return _aos.tree_digest(self.path)

    def state_dir(self, digest: str, os_name: "Optional[str]" = None) -> Path:
        """The state directory of an installed overlay at ``digest`` on
        ``os_name`` (this OS): outside the overlay root, its own."""
        return self.path.parent / self.STATE_DIR / self.name / (
            "%s-%s" % (os_name or _aos.current_os(), digest.split(":", 1)[-1][:16].lower()))

    def setup_marker(self, digest: str) -> Path:
        """Beside :meth:`state_dir`: written once setup succeeded for that digest."""
        state = self.state_dir(digest)
        return state.with_name(state.name + ".setup")

    # -- the install record ---------------------------------------------------

    @property
    def install_record_path(self) -> Path:
        return self.path.parent / self.RECORDS_DIR / (self.name + ".json")

    def read_install_record(self) -> "dict[str, Any]":
        """``{"source": {...} | None, "files": {rel: sha256}, "digest": ...,
        "commit": ...}`` of an installed overlay; empty for a source dir or an
        install without one. A record inside the overlay
        (:attr:`OLD_INSTALL_RECORD`) is read when there is none beside it."""
        import json

        for path in (self.install_record_path, self.path / self.OLD_INSTALL_RECORD):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if not isinstance(data, dict):
                continue
            files = data.get("files")
            data["files"] = {
                str(k): str(v) for k, v in files.items() if isinstance(v, str)
            } if isinstance(files, dict) else {}
            return data
        return {}

    def _write_install_record(self, record: "dict[str, Any]") -> None:
        import json

        from dotagents._fs import write_text_lf

        self.install_record_path.parent.mkdir(parents=True, exist_ok=True)
        write_text_lf(self.install_record_path, json.dumps(record, indent=1, sort_keys=True) + "\n")

    def forget(self) -> None:
        """Delete what the store keeps beside an overlay it removed: its install
        record and state directories."""
        try:
            self.install_record_path.unlink()
        except OSError:
            pass
        state = self.path.parent / self.STATE_DIR / self.name
        if state.is_dir():
            shutil.rmtree(str(state), ignore_errors=True)

    def rule_blocks(self, rel_paths: "list[str]") -> "tuple[list[str], list[str]]":
        """Extract `- **…` bullet blocks from each referenced markdown file.

        Returns (blocks, warnings). Only the leading run of bullets is taken: a
        rules file may carry explanatory prose under a `## ` heading
        (the engineering overlay's rules/ENGINEERING.md documents *why* its rules are not in the base), and
        that must not be merged into the core. A path that does not exist is
        reported, not fatal -- a broken optional overlay must never block the
        base config landing."""
        blocks: "list[str]" = []
        warnings: "list[str]" = []
        try:
            root = self.path.resolve()
        except OSError:
            root = self.path
        for rel in rel_paths:
            src = self.path / rel
            # Contained: an absolute path or one that resolves outside the
            # overlay would merge bullets from any file into AGENTS.md.
            try:
                inside = not os.path.isabs(rel) and not rel.startswith(("/", "\\")) and (
                    src.resolve() == root or root in src.resolve().parents
                )
            except OSError:
                inside = False
            if not inside:
                warnings.append("rules file outside the overlay: %s" % rel)
                continue
            if not src.is_file():
                warnings.append("rules file not found: %s" % rel)
                continue
            try:
                text = src.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as exc:
                warnings.append("rules file unreadable: %s (%s)" % (rel, exc))
                continue
            # Everything from the first bullet up to the first `## ` heading after it.
            start = re.search(r"(?m)^- \*\*", text)
            if start is None:
                warnings.append("no rules found in: %s" % rel)
                continue
            body = text[start.start():]
            end = re.search(r"(?m)^## ", body)
            if end is not None:
                body = body[: end.start()]
            blocks.append(body.rstrip())
        return blocks, warnings

    def install_to(
        self,
        dest_overlay_dir: Path,
        dry_run: bool,
        *,
        backup_root: "Optional[Path]" = None,
        source: "Optional[dict[str, Any]]" = None,
        commit: "Optional[str]" = None,
    ) -> "InstallResult":
        """Make `dest_overlay_dir` (`<scope>/overlays/<name>/`) hold exactly
        this overlay's members (:meth:`members`): each is installed or
        replaced, and everything else in the install is removed -- tool caches,
        ``__pycache__``, setup output, :attr:`OLD_INSTALL_RECORD` --
        so the installed copy has the overlay's tree digest. Setup writes into
        its state directory instead (:meth:`state_dir`).

        A member replaced, or a file removed, that is not what dotagents
        installed there (edited here, or never installed by it) is first
        copied to `backup_root` when one is given (:attr:`InstallResult.backed_up`,
        :attr:`InstallResult.replaced`); excluded entries are not backed up.

        The install record (:attr:`install_record_path`, beside the overlay)
        keeps the digest of every member written, the tree digest, `source`
        (where the overlay came from; the previous value when `None`) and the
        resolved `commit`. Raises :class:`dotagents._aos.DigestError` before
        writing anything when the overlay holds a link or a refused name."""
        from datetime import datetime, timezone

        dest = Path(dest_overlay_dir)
        installed = Overlay(dest)
        record = installed.read_install_record()
        old_files: "dict[str, str]" = record.get("files", {})
        new_files: "dict[str, str]" = {}
        result = InstallResult()
        members = self.members()
        digest = self.tree_digest()

        def local(rel: str, target: Path) -> bool:
            """Is `target` something dotagents did not put there?"""
            return old_files.get(rel) != _file_digest(target)

        def backup(rel: str, target: Path) -> None:
            if backup_root is None or dry_run:
                return
            saved = backup_root / rel
            saved.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(target), str(saved))
            result.backed_up.append(rel)

        for rel in members:
            src = self.path.joinpath(*rel.split("/"))
            target = dest.joinpath(*rel.split("/"))
            new_files[rel] = _file_digest(src)
            if target.is_file() and not os.path.islink(str(target)):
                if _same_content(src, target):
                    result.lines.append("skip (exists): %s" % rel)
                    result.unchanged += 1
                    continue
                if local(rel, target):
                    backup(rel, target)
                    result.replaced.append(rel)
                result.lines.append("update: %s" % rel)
            else:
                result.lines.append("install: %s" % rel)
            if not dry_run:
                if os.path.lexists(str(target)) and not target.is_file():
                    _remove_any(target)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(str(src), str(target))
                shutil.copymode(str(src), str(target))
            result.written += 1

        # Everything else in the install goes. An excluded directory (a
        # `.venv`, a `__pycache__`) is removed whole, never walked into.
        if dest.is_dir():
            for dirpath, dirnames, filenames in os.walk(str(dest)):
                here = Path(dirpath)
                base = here.relative_to(dest).as_posix()
                base = "" if base == "." else base
                descend = []
                for name in sorted(dirnames):
                    target = here / name
                    rel = "%s/%s" % (base, name) if base else name
                    if os.path.islink(str(target)) or name in _aos.EXCLUDED_COMPONENTS or _is_junction(target):
                        result.lines.append("remove (not part of the overlay): %s" % rel)
                        result.removed.append(rel + "/")
                        if not dry_run:
                            _remove_any(target)
                    else:
                        descend.append(name)
                dirnames[:] = descend
                for name in sorted(filenames):
                    rel = "%s/%s" % (base, name) if base else name
                    if rel in new_files:
                        continue
                    target = here / name
                    excluded = name.endswith(".pyc") or rel == self.OLD_INSTALL_RECORD
                    if not excluded and not os.path.islink(str(target)) and local(rel, target):
                        backup(rel, target)
                    result.lines.append("remove (%s): %s" % (
                        "not part of the overlay" if excluded else "not in the source", rel))
                    result.removed.append(rel)
                    if not dry_run:
                        target.unlink()
            if not dry_run:
                for dirpath, _dirnames, _filenames in os.walk(str(dest), topdown=False):
                    here = Path(dirpath)
                    if here != dest and not any(here.iterdir()):
                        here.rmdir()

        if not dry_run:
            dest.mkdir(parents=True, exist_ok=True)
            installed._write_install_record({
                "source": source if source is not None else record.get("source"),
                "commit": commit,
                "digest": digest,
                "files": new_files,
                "installed": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            })
        return result


def _is_junction(path: Path) -> bool:
    if hasattr(os.path, "isjunction"):
        return os.path.isjunction(str(path))
    try:
        return bool(getattr(os.lstat(str(path)), "st_file_attributes", 0) & 0x400)
    except OSError:
        return False


def _remove_any(path: Path) -> None:
    """Remove a file, a link (never followed) or a whole directory."""
    if os.path.islink(str(path)) or _is_junction(path):
        try:
            os.unlink(str(path))
        except OSError:
            os.rmdir(str(path))
    elif path.is_dir():
        def retry(func, p, _exc):
            os.chmod(p, 0o700)
            func(p)

        if sys.version_info >= (3, 12):
            shutil.rmtree(str(path), onexc=retry)
        else:  # pragma: no cover -- exercised on the 3.9 leg
            shutil.rmtree(str(path), onerror=retry)
    else:
        path.unlink()


# --------------------------------------------------------------------------- #
# Over a SET of overlays.
# --------------------------------------------------------------------------- #


def _compose_block(base_text: str, overlays, logger) -> str:
    """Fold each overlay's `rules`/`routing` contributions into the base block.

    Rules append to "Always-on rules" and routing to "Load on demand", after the
    base's own -- the base carries the mechanism and should read first. The
    overlays fold in **`(priority, name)` order**, NOT the caller's list
    order: lower `priority` (default `DEFAULT_PRIORITY`, 500) sorts earlier, so a
    numerically higher-priority overlay lands *last* and wins on conflict -- the
    same convention `_context.py` uses. `name` is the tiebreaker, so the block is
    deterministic regardless of discovery order. Returns `base_text` unchanged
    when nothing contributes. `overlays` are `Overlay` instances or overlay dirs."""
    from dotagents._merge import END_MARKER, _marker_lines

    def block_end(text: str) -> int:
        # The managed block's end marker line (outside fences), as `_merge`
        # matches it; the end of the text when there is none.
        ends = _marker_lines(text, END_MARKER)
        return ends[0].start() if ends else len(text)

    rules: "list[str]" = []
    routing: "list[str]" = []
    for overlay in Overlay.sort_by_priority(overlays):
        manifest = overlay.read_manifest()
        blocks, warnings = overlay.rule_blocks(manifest["rules"])  # type: ignore[arg-type]
        for warning in warnings:
            logger.warning("overlay %s: %s", manifest["name"], warning)
        rules.extend(blocks)
        routing.extend(manifest["routing"])  # type: ignore[arg-type]

    if not rules and not routing:
        return base_text

    text = base_text
    if rules:
        # Append after the last always-on bullet, i.e. just before the next heading.
        m = re.search(r"(?m)^## Load on demand", text)
        if m is None:
            # A custom `--from` base without the heading: the rules land at the
            # end of the block instead, as the warning says.
            logger.warning("base AGENTS.md has no 'Load on demand' heading; "
                           "appending overlay rules at the end of the block")
            insert_at = block_end(text)
            text = text[:insert_at] + "\n".join(rules) + "\n\n" + text[insert_at:]
        else:
            text = text[: m.start()] + "\n".join(rules) + "\n\n" + text[m.start():]
    if routing:
        # The base's placeholder line only makes sense with no routing lines.
        # Exactly that one line: anything after it is real content.
        text = re.sub(r"(?m)^Nothing ships here by default[^\n]*\n", "", text)
        # Overlay routing points at `$<NAME>_OVERLAY_ROOT/...` (the var
        # `dotagents env` exports per installed overlay) rather than a hard
        # store path; say so once, so an agent reading the file knows the
        # token is an environment variable it can resolve, not a literal path.
        if any("_OVERLAY_ROOT" in line for line in routing):
            routing = [OVERLAY_ROOT_NOTE] + routing
        insert_at = block_end(text)
        text = text[:insert_at] + "\n".join(routing) + "\n" + text[insert_at:]
    return text


#: Emitted once above overlay routing lines that use the per-overlay root vars.
OVERLAY_ROOT_NOTE = (
    "Overlay paths below use `$<NAME>_OVERLAY_ROOT` -- an environment variable "
    "already set in your shell for each installed overlay (its install dir); read "
    "just that one (`echo $ENGINEERING_OVERLAY_ROOT`; PowerShell "
    "`$env:ENGINEERING_OVERLAY_ROOT`) before opening the file with a file tool, "
    "never the whole environment (`dotagents env` prints secrets)."
)


def recompose_overlay_block(
    agents_md: Path,
    base_block: str,
    overlays: "Iterable[Overlay | str | os.PathLike[str]]",
    dry_run: bool,
    logger,
) -> bool:
    """Rebuild AGENTS.md's managed block from the *pristine* base over ALL installed
    overlays in `(priority, name)` order, in place.

    `base_block` is the base overlay's managed block (no overlay content),
    `overlays` is every installed overlay in the scope (instances or dirs), and
    `_compose_block` folds them in sorted order -- so the block is a pure
    function of *which* overlays are installed, never *when* each was added
    (an incremental append could only put a newcomer last).

    Only the managed block (between the dotagents markers) is rewritten; content
    outside the markers is untouched. Returns True if the file changed, False on a
    no-op (nothing to merge, file/markers absent, or block already correct).
    """
    from dotagents._fs import write_text_lf
    from dotagents._merge import _extract_block, find_block

    if not agents_md.is_file():
        # No `init` here yet (an `overlays add` into a fresh project): create
        # the block rather than drop the overlays' rules and routing.
        if not dry_run:
            write_text_lf(agents_md, _compose_block(_extract_block(base_block), list(overlays), logger) + "\n")
        logger.info("%s %s with the managed block", "would create" if dry_run else "created", agents_md)
        return True
    existing = agents_md.read_text(encoding="utf-8")
    span = find_block(existing)
    if span is None:
        logger.warning(
            "AGENTS.md has no dotagents managed block; overlay rules/routing not "
            "merged (run `dotagents init` first)"
        )
        return False

    # Compose over the pristine base block, not the current (already-merged) one, so
    # every install of the same overlay set yields identical output.
    pristine = _extract_block(base_block)
    merged = _compose_block(pristine, list(overlays), logger)

    start, end = span
    if existing[start:end] == merged:
        return False
    new_text = existing[:start] + merged + existing[end:]
    if not dry_run:
        write_text_lf(agents_md, new_text)
    return True
