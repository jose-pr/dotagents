"""Scope and overlay-source resolution for ``dotagents overlays``.

Two orthogonal axes the ``overlays`` command needs, kept out of ``cli.py`` (which
only wires args) to match ``_overlays.py`` / ``_skills.py``:

* **Scope** -- *where installed overlays live*. ``user`` is ``<agents_dir>/`` (the
  configurable store, default ``~/.agents``); ``project`` is ``<project>/.agents/``.
  An overlay installs into ``<scope>/overlays/<name>/`` and skills publish into the
  shared ``<scope>/skills/``. There is no registry file: installed overlays are
  **discovered** by their presence under ``overlays/`` (the locked "discover, don't
  track" decision).

* **Source** -- *where an overlay to install comes from*. ``resolve_source`` returns
  the repos in precedence order (``--repo``, the env repos, the stores'
  ``dotagents.*`` registries, the bundled ``overlays/`` -- resolved
  ``.pyz``-safe via ``importlib.resources``, mirroring ``cli._package_data_dir``);
  a repo is a directory of overlays, a registry file, or a git spec, and the
  first offering a name wins. The command classes only use the returned
  object's ``available`` / ``overlay_dir`` / ``root`` -- see ``dotagents._sources``.

The ``system`` store (``Scope.system_root``: ``/etc/agents``, or
``$AGENTS_SYSTEM_ROOT``) is walked by the Contract-A resolver (``Scope.paths``)
for overlays/env/context/bin/cmds like any store, first in precedence, but
nothing installs into it -- there is no ``--system`` scope for ``init`` /
``overlays``.

Never print ``DOTAGENTS_*`` values (Leakage): this module reads the env var but
only ever reports the resolved path, never the raw value.
"""

from __future__ import annotations

import fnmatch
import os
from pathlib import Path
from typing import Optional

from dotagents._overlays import Overlay

#: Level names the walk itself uses. An overlay's DIRECTORY NAME is its level
#: label, so an overlay named like one of these would collide with the per-level
#: name-dict keys (`{"project-root": ""}` would drop that overlay's `bin`);
#: `Overlay.is_valid_name` rejects them.
LEVEL_NAMES = frozenset({"default", "overlay", "system", "user", "project", "project-root"})


class Scope:
    """Where a session's config lives -- the one object every walk takes.

    ``level`` is ``"user"`` or ``"project"`` and ``agents_root`` the scope's own
    store (the install target of ``overlays add`` / ``init``): ``<agents_dir>/``
    (the configurable store, default ``~/.agents``) for the user scope,
    ``<project_root>/.agents/`` for a project. A project scope also knows the
    ``user_root`` (the user store -- every walk starts from it) and the
    ``project_root``; ``stores`` is the pair in precedence order, and
    :attr:`overlays` is :meth:`Overlay.installed` over them (a same-named
    project overlay shadows the store's copy). The user scope has one store.
    :meth:`paths` is the contract-A walk for this scope; ``env``, ``context``
    and ``cli._cmds_dirs`` all go through it.

    The ``overlays/`` and ``skills/`` subdirs beneath ``agents_root`` are the
    discover-and-publish surfaces of THIS scope (``Overlay.discover(scope.overlay_root)``).
    """

    def __init__(
        self,
        level: str,
        agents_root: "str | os.PathLike[str]",
        *,
        user_root: "str | os.PathLike[str] | None" = None,
        project_root: "str | os.PathLike[str] | None" = None,
        system_root: "str | os.PathLike[str] | None" = None,
    ):
        self.level = level
        self.agents_root = Path(agents_root)
        #: The machine-wide store (``/etc/agents``, or ``$AGENTS_SYSTEM_ROOT``):
        #: walked for overlays/bin/lib/env/cmds/AGENTS.md like any store, first
        #: in precedence; nothing installs into it (no ``--system`` scope).
        self.system_root = Path(system_root) if system_root else system_root_default()
        if level == "user":
            self.user_root = self.agents_root
            self.project_root: "Optional[Path]" = None
        else:
            self.user_root = Path(user_root) if user_root else resolve_user_store()
            self.project_root = (
                Path(project_root) if project_root else self.agents_root.parent
            )

    @classmethod
    def of(
        cls,
        *,
        agents_dir: "str | os.PathLike[str]",
        project_root: "str | os.PathLike[str] | None" = None,
        global_scope: bool = False,
    ) -> "Scope":
        """The walk's scope from its parts: ``agents_dir`` (the user store),
        ``project_root`` and ``global_scope`` -- what ``env`` / ``context`` build
        after resolving each part (``resolve_user_store``,
        ``project_root_default``, ``-g``)."""
        if global_scope or project_root is None:
            return cls("user", agents_dir)
        return cls(
            "project", Path(project_root) / ".agents",
            user_root=agents_dir, project_root=project_root,
        )

    @property
    def global_scope(self) -> bool:
        return self.level == "user"

    @property
    def system_store(self) -> Path:
        """Alias of :attr:`system_root`, for symmetry with :attr:`project_store`."""
        return self.system_root

    @property
    def project_store(self) -> "Optional[Path]":
        """The project's store (``agents_root``) -- ``None`` in the user scope."""
        return None if self.global_scope else self.agents_root

    @property
    def stores(self) -> "list[Path]":
        """The stores in play, in precedence order: the system store, the user
        store, then (in a project scope) the project's."""
        stores = [self.system_root, self.user_root]
        if not self.global_scope:
            stores.append(self.agents_root)
        return stores

    def store_level(self, store: "str | os.PathLike[str]") -> str:
        """The contract-A level name of one of :attr:`stores`: ``system``,
        ``user`` or ``project``."""
        store = Path(store)
        if store == self.system_root:
            return "system"
        if store == self.user_root:
            return "user"
        if store == self.project_store:
            return "project"
        raise ValueError("%s is not a store of %r" % (store, self))

    @property
    def overlays(self) -> "list[Overlay]":
        """Every overlay a session in this scope uses (:meth:`Overlay.installed`
        over :attr:`stores`: the project's copy shadows a same-named store copy)."""
        return Overlay.installed(*self.stores)

    def paths(
        self, *names: "str | dict[str, str]", include_missing: bool = False
    ) -> "list[tuple[str, Path, Optional[Path]]]":
        """Resolve file paths across this scope's precedence hierarchy (Contract A).

        Each ``name`` is a filename (resolved at every level) or a per-level
        dict -- ``{"default": ..., "overlay": ..., "<level>": ...}`` -- where an
        empty / missing entry skips that level.

        Precedence order -- each of :attr:`stores` in turn, its overlays first,
        then the store itself; finally the project root:
        1. system overlays, then system (:attr:`system_root`)
        2. user-store overlays, then user (:attr:`user_root`)
        3. project overlays (``<project_root>/.agents/overlays/<name>/``), then
           project (``<project_root>/.agents``) -- a project scope only
        4. project-root (:attr:`project_root`) -- a project scope only

        An overlay installed in more than one store under the same name is one
        overlay, the later store's: it SHADOWS the earlier copies entirely
        (:meth:`Overlay.installed`), so its bin/lib/env/cmds/CONTEXT.md are the
        only ones that resolve -- not stacked.

        Each returned tuple is ``(level, path, root)``: for an overlay,
        ``level`` is the overlay's directory name and ``root`` its directory;
        for every other level ``root`` is ``None`` -- that is how callers tell
        overlays apart. ``include_missing`` returns every candidate, else only
        the ones that exist.
        """
        found: "list[tuple[str, Path, Optional[Path]]]" = []

        def add(location: Path, level: str, root: "Optional[Path]" = None,
                is_overlay: bool = False) -> None:
            for name in names:
                name_dict = {level: name} if isinstance(name, str) else name
                default = name_dict.get("default")
                if is_overlay:
                    default = name_dict.get("overlay", default)
                template = name_dict.get(level, default)
                if template:
                    found.append((level, location / template, root))

        # No manifest of any kind is required for an overlay to count -- not
        # ``CONTEXT.md``, not ``overlay.toml`` (the old ``CONTEXT.md`` gate was a
        # precursor leftover that silently excluded EVERY real overlay, D84).
        overlays = self.overlays  # shadowing already applied, store-stamped
        for store in self.stores:
            for overlay in overlays:
                if overlay.store == store:
                    add(overlay.path, overlay.name, root=overlay.path, is_overlay=True)
            add(store, self.store_level(store))
        if not self.global_scope:
            add(self.project_root, "project-root")  # type: ignore[arg-type]

        if include_missing:
            return found
        return [(level, path, root) for level, path, root in found if path.exists()]

    @property
    def overlay_root(self) -> Path:
        return self.agents_root / "overlays"

    @property
    def shared_skills_dir(self) -> Path:
        return self.agents_root / "skills"

    @property
    def cmds_dir(self) -> Path:
        """Directory of discovered command modules for this scope (D76).

            ``<agents_root>/dotagents/cmds`` -- a seam alongside ``overlays``/``skills``.
        ``init`` creates it (with the README only; the bundled modules are always
        discovered from the package itself), and ``dotagents.cli._discover`` runs
        ``duho.discover_commands`` over it (per scope, user + project) so a
        user's own ``*.py`` command modules dropped here are picked up with zero
        config."""
        return self.agents_root / "dotagents" / "cmds"

    def overlay_dir(self, name: str) -> Path:
        return self.overlay_root / name

    def __repr__(self) -> str:
        if self.global_scope:
            return "Scope(user, root=%s)" % self.agents_root
        return "Scope(project, root=%s, user_root=%s)" % (self.agents_root, self.user_root)



def resolve_scope(
    global_scope: bool = False,
    *,
    agents_dir: "str | os.PathLike | None" = None,
    project_root: "str | os.PathLike | None" = None,
) -> Scope:
    """Pick the install scope.

    ``-g/--global`` forces the **user** scope: ``agents_dir`` (``--agents-dir``)
    if given, else the configurable store -- ``$AGENTS_HOME``, then
    ``~/.agents`` (:func:`resolve_user_store`,
    the same chain ``env`` / ``context`` use, so a session that pinned the store
    installs into it rather than into the literal home dir).
    Otherwise the scope is **project**: ``agents_dir`` if given (the store root
    override applies to either scope), else ``<project_root>/.agents`` where the
    root is (in precedence order) an explicit ``project_root`` argument, else
    ``$AGENTS_PROJECT_ROOT`` if set, else the current directory.
    ``$AGENTS_PROJECT_ROOT`` lets a harness (or ``dotagents env``) pin the project
    root once so every command agrees on it regardless of the cwd a subprocess
    happens to run in; ``<root>/.agents/`` is where this project's overlays live.
    """
    if global_scope:
        return Scope("user", resolve_user_store(agents_dir))
    proj = Path(project_root).expanduser() if project_root else project_root_default()
    if agents_dir:
        return Scope("project", Path(agents_dir).expanduser(), project_root=proj)
    return Scope("project", proj / ".agents", project_root=proj)


#: The machine-wide store's location; read, never printed. Defaults to
#: ``/etc/agents`` (a Windows path when set on Windows).
SYSTEM_ROOT_ENV = "AGENTS_SYSTEM_ROOT"


def system_root_default() -> Path:
    """``$AGENTS_SYSTEM_ROOT`` if set, else ``/etc/agents``."""
    value = os.environ.get(SYSTEM_ROOT_ENV)
    return Path(value).expanduser() if value else Path("/etc/agents")


#: The configurable user-scope store (D58). Every reader of the user store
#: resolves it through this var (default `~/.agents`) rather than hardcoding the
#: home path -- this is the same var `dotagents env` emits (D79). Re-exported by
#: `dotagents.cli` for command modules.
AGENTS_DIR_ENV = "AGENTS_HOME"


def resolve_user_store(agents_dir: "str | os.PathLike | None" = None) -> Path:
    """The USER store root, in precedence order: an explicit ``agents_dir``
    (``--agents-dir``) -> ``$AGENTS_HOME`` -> ``~/.agents`` (D58/D79).

    :func:`resolve_scope` defaults its ``-g`` store through this; ``env`` and
    ``context`` -- whose Contract-A walk takes the user store as ``agents_dir``
    and the project root separately, and whose ``-g`` only *skips* the project
    tiers -- call it directly, so the store never becomes the project dir.

    Never logs or prints the raw env value (Leakage rule); only the resolved path
    is ever reported.
    """
    if agents_dir:
        return Path(agents_dir).expanduser()
    value = os.environ.get(AGENTS_DIR_ENV)
    if value:
        return Path(value).expanduser()
    return Path.home() / ".agents"


#: Agent-native project-root vars, consulted (in order) as a fallback for
#: ``AGENTS_PROJECT_ROOT``. A harness that already exposes its workspace root lets
#: dotagents pick it up without the user setting anything. As of 2026-07, Claude
#: Code's ``CLAUDE_PROJECT_DIR`` is the ONLY one any major harness sets (and only in
#: hook / stdio-MCP / plugin-LSP contexts, not its Bash tool) -- Gemini/Codex/Cursor/
#: Copilot/aider all rely on cwd + internal git-root detection and export nothing.
#: Extend this tuple as other harnesses adopt one.
_HARNESS_PROJECT_ROOT_VARS = ("CLAUDE_PROJECT_DIR",)


def project_root_default() -> Path:
    """The project root when none is passed explicitly, in precedence order:
    ``$AGENTS_PROJECT_ROOT`` (dotagents' canonical var) -> a known agent-native var
    (:data:`_HARNESS_PROJECT_ROOT_VARS`, e.g. Claude Code's ``CLAUDE_PROJECT_DIR``)
    -> the current working directory.

    Emitting ``AGENTS_PROJECT_ROOT`` (see ``dotagents env``) lets every command and
    subprocess agree on one root regardless of the cwd it happens to run in."""
    for var in ("AGENTS_PROJECT_ROOT", *_HARNESS_PROJECT_ROOT_VARS):
        value = os.environ.get(var)
        if value:
            return Path(value).expanduser()
    return Path.cwd()


def filter_names(names: "list[str]", pattern: "Optional[str]") -> "list[str]":
    """Glob-filter overlay names (``sync 'py*'``). ``None``/``'*'`` keep all."""
    if not pattern or pattern == "*":
        return list(names)
    return [n for n in names if fnmatch.fnmatch(n, pattern)]


def OverlaySource(root):  # noqa: N802 -- the name tests and older code use
    """A directory of overlays (``<root>/<name>/``): :class:`dotagents._sources.DirRepo`."""
    from dotagents._sources import DirRepo

    return DirRepo(root)




def bundled_overlays_root() -> "Path | None":
    """Locate the bundled example ``overlays/`` directory, ``.pyz``-safe.

    Two homes, tried in order: the packaged copy (``importlib.resources`` under the
    installed ``dotagents`` package -- extracted from a zipapp when needed, exactly
    like ``cli._package_data_dir``), then a repo checkout's top-level ``overlays/``
    (dev use, mirroring ``BuildPyz``'s ``parents[2]`` reach). Returns ``None`` if
    neither exists -- a plain ``pip install`` that bundled no overlays.
    """
    # Prefer the shared resolver in cli so zipapp extraction is cached once.
    try:
        from dotagents.cli import _package_data_dir

        packaged = _package_data_dir("_overlays_src")
        if packaged is not None and packaged.is_dir():
            return packaged
    except Exception:
        pass

    repo_overlays = Path(__file__).resolve().parents[2] / "overlays"
    if repo_overlays.is_dir():
        return repo_overlays
    return None


def resolve_source(
    repos: "Optional[list[str]]" = None,
    *,
    scope: "Optional[Scope]" = None,
    logger=None,
):
    """Resolve where overlays come from: the repos in precedence order --
    ``repos`` (``--repo`` values), then ``$AGENTS_OVERLAYS_REPO_<KEY>`` by KEY,
    then ``$AGENTS_OVERLAYS_REPO``, then the project and user stores'
    ``dotagents.{json,toml,yaml,yml}`` registries, then this build's bundled
    ``overlays/`` -- the first repo offering a name wins. A repo is a directory of overlays, a registry file, or a git spec
    (``<repo>[@ref][#path]``) that materializes to one; see
    :mod:`dotagents._sources`. Callers use only ``available()`` /
    ``overlay_dir()`` / ``root``. Raises when nothing at all is configured."""
    from dotagents import _sources

    user_root = Path(scope.user_root) if scope is not None else resolve_user_store(None)
    stores = [scope.project_store if scope is not None and not scope.global_scope else None, user_root]
    return _sources.resolve(
        list(repos or []),
        cache_root=user_root / ".cache" / "overlays",
        stores=stores,
        bundled=bundled_overlays_root(),
        logger=logger,
    )
