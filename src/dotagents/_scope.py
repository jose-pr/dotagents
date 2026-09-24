"""Scope and overlay-source resolution for ``dotagents overlays``.

Two orthogonal axes the ``overlays`` command needs, kept out of ``cli.py`` (which
only wires args) to match ``_overlays.py`` / ``_skills.py``:

* **Scope** -- *where installed overlays live*. ``user`` is ``<agents_dir>/`` (the
  configurable store, default ``~/.agents``); ``project`` is ``<project>/.agents/``.
  An overlay installs into ``<scope>/overlays/<name>/`` and skills publish into the
  shared ``<scope>/skills/``. There is no registry file: installed overlays are
  **discovered** by their presence under ``overlays/``.

* **Source** -- *where an overlay to install comes from*. ``resolve_source`` returns
  the repos in precedence order (``--repo``, the env repos, the project and
  user stores' ``dotagents.*`` registries; no build bundles overlays); a repo
  is a directory of overlays, a registry file, or a git spec, and the first
  offering a name wins. See ``dotagents._sources`` for the returned object.

The ``system`` store (``Scope.system_root``: ``/etc/agents`` on POSIX, or
``$AGENTS_SYSTEM_ROOT``; only when administrators alone can write it) is walked by the Contract-A resolver (``Scope.paths``)
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


def _path_key(path: "str | os.PathLike[str]") -> str:
    """A comparison key for ``path``: resolved (symlinks, ``..``) where the
    filesystem allows, case-folded where the platform is case-insensitive."""
    try:
        resolved = str(Path(path).expanduser().resolve())
    except (OSError, RuntimeError):
        resolved = os.path.abspath(str(path))
    return os.path.normcase(resolved)


def _same_path(a: "str | os.PathLike[str]", b: "str | os.PathLike[str]") -> bool:
    """Whether ``a`` and ``b`` name the same directory."""
    return _path_key(a) == _path_key(b)


def _is_within(path: "str | os.PathLike[str]", root: "str | os.PathLike[str]") -> bool:
    """Whether ``path`` is ``root`` or somewhere beneath it."""
    key, root_key = _path_key(path), _path_key(root)
    if key == root_key:
        return True
    return key.startswith(root_key.rstrip(os.sep) + os.sep)


def _listing(directory: Path) -> "Optional[tuple[str, ...]]":
    """The sorted entry names of ``directory``, or None when it is not one."""
    try:
        return tuple(sorted(os.listdir(str(directory))))
    except OSError:
        return None


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

    A project whose ``.agents`` is the user store (the project root is ``~``)
    is constructed as the user scope, keeping ``project_root`` for the
    project-root level, so the store is walked once; :attr:`stores` also drops
    any directory that appears twice.
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
        #: The machine-wide store (``/etc/agents`` on POSIX, or
        #: ``$AGENTS_SYSTEM_ROOT``; ``None`` when there is none, see
        #: :func:`system_root_default`): walked for overlays/bin/lib/env/cmds/
        #: AGENTS.md like any store, first in precedence; nothing installs into
        #: it (no ``--system`` scope).
        self.system_root: "Optional[Path]" = (
            Path(system_root) if system_root else system_root_default()
        )
        if level == "user":
            self.user_root = self.agents_root
            #: Set on a user scope only when a project's store turned out to BE
            #: the user store (below): the project-root level is still walked.
            self.project_root: "Optional[Path]" = (
                Path(project_root) if project_root else None
            )
        else:
            self.user_root = Path(user_root) if user_root else resolve_user_store()
            self.project_root = (
                Path(project_root) if project_root else self.agents_root.parent
            )
            if _same_path(self.agents_root, self.user_root):
                # A project rooted at the user store's parent (a session started
                # in ~): its `.agents` IS the user store. Walking it as a second,
                # "project" store ran every env.py twice and emitted every
                # AGENTS.md / CONTEXT.md twice; it is the user scope, keeping
                # the project root for the project-root level.
                self.level = "user"
                self.agents_root = self.user_root

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
    def system_store(self) -> "Optional[Path]":
        """Alias of :attr:`system_root`, for symmetry with :attr:`project_store`."""
        return self.system_root

    @property
    def project_store(self) -> "Optional[Path]":
        """The project's store (``agents_root``) -- ``None`` in the user scope."""
        return None if self.global_scope else self.agents_root

    @property
    def stores(self) -> "list[Path]":
        """The stores in play, in precedence order: the system store (when
        there is one), the user store, then (in a project scope) the project's."""
        candidates = [self.system_root] if self.system_root is not None else []
        candidates.append(self.user_root)
        if not self.global_scope:
            candidates.append(self.agents_root)
        # The same directory twice (a project whose `.agents` is the system
        # store) is walked once, at its first -- broader -- level.
        stores: "list[Path]" = []
        for store in candidates:
            if not any(_same_path(store, kept) for kept in stores):
                stores.append(store)
        return stores

    def store_level(self, store: "str | os.PathLike[str]") -> str:
        """The contract-A level name of one of :attr:`stores`: ``system``,
        ``user`` or ``project``."""
        store = Path(store)
        if self.system_root is not None and store == self.system_root:
            return "system"
        if store == self.user_root:
            return "user"
        if store == self.project_store:
            return "project"
        raise ValueError("%s is not a store of %r" % (store, self))

    @property
    def overlays(self) -> "list[Overlay]":
        """Every overlay a session in this scope uses (:meth:`Overlay.installed`
        over :attr:`stores`: the project's copy shadows a same-named store copy).

        Memoized on the stores and their ``overlays/`` listings: one ``env``
        run walks :meth:`paths` several times, and each rescan checked every
        entry; an overlay added or removed since (``overlays add`` reads this
        again after installing) changes a listing and is seen."""
        stores = self.stores
        key = tuple((str(store), _listing(Path(store) / "overlays")) for store in stores)
        memo = self.__dict__.get("_overlays_memo")
        if memo is None or memo[0] != key:
            memo = (key, Overlay.installed(*stores))
            self.__dict__["_overlays_memo"] = memo
        return list(memo[1])

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
        4. project-root (:attr:`project_root`) -- whenever there is one: a
           project scope, or the user scope a project rooted at ``~`` becomes

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

        # No manifest is required for an overlay to count -- neither
        # ``CONTEXT.md`` nor ``overlay.toml`` (D84).
        overlays = self.overlays  # shadowing already applied, store-stamped
        for store in self.stores:
            for overlay in overlays:
                if overlay.store == store:
                    add(overlay.path, overlay.name, root=overlay.path, is_overlay=True)
            add(store, self.store_level(store))
        if self.project_root is not None:
            add(self.project_root, "project-root")

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
        """Directory of discovered command modules for this scope:
        ``<agents_root>/dotagents/cmds``, a seam alongside ``overlays``/``skills``.

        ``init`` never creates it -- the dir exists once a user adds a command
        module (the bundled modules are discovered from the package itself).
        Discovery (``dotagents.cli._cmds_dirs``) walks the same
        ``dotagents/cmds`` name at every level through :meth:`paths` and skips
        a missing dir, so a user's own ``*.py`` command modules dropped here are
        picked up with zero config."""
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
    root is an explicit ``project_root`` argument, else
    :func:`install_project_root` -- the pinned root (``$AGENTS_PROJECT_ROOT``,
    then ``$CLAUDE_PROJECT_DIR``) while the current directory is inside it, and
    the project the current directory is in once it has left it. A project whose
    ``.agents`` is the user store (the root is ``~``) is the user scope.
    """
    if global_scope:
        return Scope("user", resolve_user_store(agents_dir))
    if agents_dir:
        # An explicit store is the write target; the pin only names the root.
        proj = Path(project_root).expanduser() if project_root else project_root_default()
        return Scope("project", Path(agents_dir).expanduser(), project_root=proj)
    proj = Path(project_root).expanduser() if project_root else install_project_root()
    return Scope("project", proj / ".agents", project_root=proj)


#: The machine-wide store's location; read, never printed. Defaults to
#: ``/etc/agents`` on POSIX; Windows has no default (D93).
SYSTEM_ROOT_ENV = "AGENTS_SYSTEM_ROOT"

_system_root_cache: "dict[str, Optional[Path]]" = {}

#: Windows SIDs allowed to hold write access on a system store: Administrators,
#: SYSTEM, TrustedInstaller. Any other allow-write ACE (Users, Authenticated
#: Users, Everyone, a single user's SID) makes the store untrusted.
_WINDOWS_TRUSTED_SIDS = (
    "S-1-5-32-544",
    "S-1-5-18",
    "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464",
)

_WINDOWS_ACL_CHECK = r"""
$ErrorActionPreference = 'Stop'
$ok = @(%(sids)s)
$w = [System.Security.AccessControl.FileSystemRights]'WriteData,AppendData,WriteExtendedAttributes,WriteAttributes,Delete,DeleteSubdirectoriesAndFiles,ChangePermissions,TakeOwnership'
$rules = (Get-Acl -LiteralPath '%(path)s').GetAccessRules($true, $true, [System.Security.Principal.SecurityIdentifier])
foreach ($r in $rules) {
  if ($r.AccessControlType -ne 'Allow') { continue }
  if ($r.PropagationFlags -band [System.Security.AccessControl.PropagationFlags]::InheritOnly) { continue }
  if (($r.FileSystemRights -band $w) -and ($ok -notcontains $r.IdentityReference.Value)) {
    Write-Output $r.IdentityReference.Value; exit 1
  }
}
exit 0
"""


def _is_windows() -> bool:
    """Seam: tests patch this, never ``os.name``."""
    return os.name == "nt"


def _system_root_is_safe(path: Path) -> "tuple[bool, str]":
    """Whether only administrators can write ``path`` -- the bar for a store
    whose ``env.py`` and ``cmds/`` run in every user's session. POSIX: owned by
    root and not group/world-writable. Windows: no allow-write ACE for any SID
    but Administrators, SYSTEM or TrustedInstaller. Returns ``(ok, reason)``."""
    if not _is_windows():
        try:
            st = os.stat(str(path))
        except OSError as exc:
            return False, "cannot stat it (%s)" % exc
        if st.st_uid != 0:
            return False, "it is not owned by root"
        if st.st_mode & 0o022:
            return False, "it is group- or world-writable"
        return True, ""
    import subprocess

    script = _WINDOWS_ACL_CHECK % {
        "sids": ", ".join("'%s'" % sid for sid in _WINDOWS_TRUSTED_SIDS),
        "path": str(path).replace("'", "''"),
    }
    # By absolute path: a bare "powershell" is looked up in the current
    # directory before System32. And without PSModulePath: a PowerShell 7
    # parent exports its own, from which Windows PowerShell cannot load
    # Get-Acl, so every store was rejected when dotagents ran from pwsh.
    system_root = os.environ.get("SystemRoot") or r"C:\Windows"
    exe = os.path.join(system_root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
    env = {k: v for k, v in os.environ.items() if k.upper() != "PSMODULEPATH"}
    try:
        res = subprocess.run(
            [exe, "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=30, env=env,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, "its ACL could not be read (%s)" % exc
    if res.returncode == 0:
        return True, ""
    who = res.stdout.strip() or res.stderr.strip()[:200]
    return False, "a non-administrator can write it (%s)" % (who or "unreadable ACL")


def system_root_default() -> "Optional[Path]":
    """The machine-wide store, or ``None`` when there is none to walk.

    ``$AGENTS_SYSTEM_ROOT`` when set (it must be an absolute path), else
    ``/etc/agents`` on POSIX and nothing on Windows -- there ``/etc/agents`` is
    drive-relative (``\\etc\\agents``), and any local account can create it.
    A root that does not exist is ``None`` too, so nothing of it (a
    ``bin/`` on PATH included) is walked. A root that a non-administrator can
    write is skipped with a warning: its ``env.py`` and ``cmds/`` would run in
    every user's session. Resolved once per process and value."""
    value = os.environ.get(SYSTEM_ROOT_ENV) or ""
    if value in _system_root_cache:
        return _system_root_cache[value]
    import logging

    log = logging.getLogger("dotagents")
    root: "Optional[Path]" = None
    if value:
        candidate = Path(value).expanduser()
        if not candidate.is_absolute():
            log.warning("ignoring $%s=%r: not an absolute path", SYSTEM_ROOT_ENV, value)
            candidate = None
    else:
        candidate = None if _is_windows() else Path("/etc/agents")
    if candidate is not None and candidate.is_dir():
        ok, reason = _system_root_is_safe(candidate)
        if ok:
            root = candidate
        else:
            log.warning("ignoring the system store %s: %s", candidate, reason)
    _system_root_cache[value] = root
    return root


#: The configurable user-scope store (D58). Every reader of the user store
#: resolves it through this var (default `~/.agents`) rather than hardcoding the
#: home path -- this is the same var `dotagents env` emits (D79). Re-exported by
#: `dotagents.cli` for command modules.
AGENTS_DIR_ENV = "AGENTS_HOME"


def resolve_user_store(agents_dir: "str | os.PathLike | None" = None) -> Path:
    """The USER store root, in precedence order: an explicit ``agents_dir``
    (``--agents-dir``) -> ``$AGENTS_HOME`` -> ``~/.agents``.

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
#: ``AGENTS_PROJECT_ROOT``, so a harness that exposes its workspace root is
#: picked up without the user setting anything. Claude Code sets
#: ``CLAUDE_PROJECT_DIR`` only in hook / stdio-MCP / plugin-LSP contexts, not
#: its Bash tool. Extend this tuple as other harnesses adopt one.
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


#: Where :func:`_project_containing` stops walking up (inclusive); tests set
#: it so a temp dir under the real home never reaches that home's ``.agents``.
_walk_stop: "Optional[Path]" = None


def _project_containing(start: Path) -> Path:
    """The nearest directory at or above ``start`` that is a project root -- one
    holding a ``.git`` or a ``.agents`` of its own -- else ``start`` itself.

    The home directory and everything above it never count (``~/.agents`` is a
    user store even when ``$AGENTS_HOME`` names another), and neither does the
    parent of the user or system store: its ``.agents`` is a store every
    session shares."""
    shared = [resolve_user_store()]
    system = system_root_default()
    if system is not None:
        shared.append(system)
    home = Path.home()
    chain = [start, *start.parents]
    if _walk_stop is not None:
        stop = Path(_walk_stop)
        chain = [d for d in chain if _is_within(d, stop)]
    for candidate in chain:
        if _is_within(home, candidate):  # home itself, or above it
            break
        if (candidate / ".git").exists():
            return candidate
        dot_agents = candidate / ".agents"
        if dot_agents.is_dir() and not any(_same_path(dot_agents, s) for s in shared):
            return candidate
    return start


def install_project_root() -> Path:
    """The project root the WRITING commands (``init``, ``overlays``,
    ``findings``) target when none is passed explicitly.

    A pinned root (``$AGENTS_PROJECT_ROOT``, then the harness vars of
    :data:`_HARNESS_PROJECT_ROOT_VARS`) is honoured only while the current
    directory is inside it. A hooked session exports the pin once for its whole
    life, so after ``cd ../other`` the pin still names the FIRST project; a
    command that writes then targets where it runs -- the nearest ancestor of
    the cwd with a ``.git`` or its own ``.agents``, else the cwd -- and says so
    with a warning. With no pin, the cwd.

    ``env`` / ``context`` keep :func:`project_root_default`, where the pin wins
    regardless of the cwd: what they read must stay stable across the
    subdirectories a session's commands run in."""
    cwd = Path.cwd()
    for var in ("AGENTS_PROJECT_ROOT", *_HARNESS_PROJECT_ROOT_VARS):
        value = os.environ.get(var)
        if not value:
            continue
        pinned = Path(value).expanduser()
        if _is_within(cwd, pinned):
            return pinned
        found = _project_containing(cwd)
        import logging

        logging.getLogger("dotagents").warning(
            "the current directory is outside the pinned project root %s ($%s); "
            "using %s", pinned, var, found,
        )
        return found
    return cwd


def filter_names(names: "list[str]", pattern: "Optional[str]") -> "list[str]":
    """Glob-filter overlay names (``sync 'py*'``). ``None``/``'*'`` keep all."""
    if not pattern or pattern == "*":
        return list(names)
    return [n for n in names if fnmatch.fnmatch(n, pattern)]


def OverlaySource(root):  # noqa: N802 -- reads as a class at the call site
    """A directory of overlays (``<root>/<name>/``): :class:`dotagents._sources.DirRepo`."""
    from dotagents._sources import DirRepo

    return DirRepo(root)



def resolve_source(
    repos: "Optional[list[str]]" = None,
    *,
    scope: "Optional[Scope]" = None,
    logger=None,
    allow_empty: bool = False,
):
    """Resolve where overlays come from: the repos in precedence order --
    ``repos`` (``--repo`` values), then ``$AGENTS_OVERLAYS_REPO_<KEY>`` by KEY,
    then ``$AGENTS_OVERLAYS_REPO``, then the project and user stores'
    ``dotagents.{json,toml,yaml,yml}`` registries -- the first repo offering a
    name wins. A repo is a directory of overlays, a registry file, or a git spec
    (``<repo>[@ref][#path]``) that materializes to one; see
    :mod:`dotagents._sources`. No build bundles overlays: they live on the
    ``repo`` branch and are named as a repo like any other. Callers use only
    ``available()`` / ``overlay_dir()`` / ``root``. Raises when nothing at all
    is configured, unless ``allow_empty``."""
    from dotagents import _sources

    user_root = Path(scope.user_root) if scope is not None else resolve_user_store(None)
    stores = [scope.project_store if scope is not None and not scope.global_scope else None, user_root]
    return _sources.resolve(
        list(repos or []),
        cache_root=user_root / ".cache" / "overlays",
        stores=stores,
        logger=logger,
        allow_empty=allow_empty,
    )
