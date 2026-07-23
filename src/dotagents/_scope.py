"""Scope and overlay-source resolution for ``dotagents overlays``.

Two orthogonal axes the ``overlays`` command needs, kept out of ``cli.py`` (which
only wires args) to match ``_overlays.py`` / ``_skills.py`` / ``_link.py``:

* **Scope** -- *where installed overlays live*. ``user`` is ``<agents_dir>/`` (the
  configurable store, default ``~/.agents``); ``project`` is ``<project>/.agents/``.
  An overlay installs into ``<scope>/overlays/<name>/`` and skills publish into the
  shared ``<scope>/skills/``. There is no registry file: installed overlays are
  **discovered** by their presence under ``overlays/`` (the locked "discover, don't
  track" decision). A ``system`` tier (``/etc/agents``) is designed-for but not built.

* **Source** -- *where an overlay to install comes from*. ``resolve_source`` returns
  an ``OverlaySource`` whose ``.root`` is a local directory of ``<name>/`` overlay
  dirs. The default is the bundled ``overlays/`` (resolved ``.pyz``-safe via
  ``importlib.resources``, mirroring ``cli._package_data_dir``); an explicit
  ``--source`` / ``$DOTAGENTS_OVERLAYS_SRC`` overrides it. A git/URI source is a
  *later* swap of the resolver's default -- ``resolve_source`` is the single
  extension point (clone/pull into a cache, then hand back a local ``.root``), so a
  repo source drops in with **no** change to the command classes.

Never print ``DOTAGENTS_*`` values (Leakage): this module reads the env var but
only ever reports the resolved path, never the raw value.
"""

from __future__ import annotations

import fnmatch
import os
from pathlib import Path
from typing import Optional


class Scope:
    """A resolved install scope: the roots overlays and skills live under.

    ``user`` -> ``<agents_dir>/`` (the configurable store, default ``~/.agents``).
    ``project`` -> ``<project_root>/.agents/``. The ``overlays/`` and ``skills/``
    subdirs beneath ``agents_root`` are the discover-and-publish surfaces.
    """

    def __init__(self, level: str, agents_root: Path):
        self.level = level
        self.agents_root = Path(agents_root)

    @property
    def overlay_root(self) -> Path:
        return self.agents_root / "overlays"

    @property
    def shared_skills_dir(self) -> Path:
        return self.agents_root / "skills"

    def overlay_dir(self, name: str) -> Path:
        return self.overlay_root / name

    def __repr__(self) -> str:
        return "Scope(%s, root=%s)" % (self.level, self.agents_root)


def resolve_scope(
    global_scope: bool = False,
    *,
    agents_dir: "str | os.PathLike | None" = None,
    project_root: "str | os.PathLike | None" = None,
) -> Scope:
    """Pick the install scope.

    ``-g/--global`` forces the **user** scope (``agents_dir``, default ``~/.agents``).
    Otherwise the scope is **project** when a project is in play (an explicit
    ``project_root`` or -- for a real invocation -- the current directory), which is
    where ``<project>/.agents/`` overlays belong. The store location is configurable
    (D58): ``agents_dir`` comes from the caller (``--agents-dir``) or defaults to
    ``~/.agents``; it is never hardcoded past this default.
    """
    root = Path(agents_dir).expanduser() if agents_dir else (Path.home() / ".agents")
    if global_scope:
        return Scope("user", root)
    proj = Path(project_root).expanduser() if project_root else Path.cwd()
    return Scope("project", proj / ".agents")


def discover_overlays(scope: Scope) -> "list[str]":
    """Installed overlay names in this scope -- the presence of ``overlays/<name>/``.

    No registry: a directory under ``<scope>/overlays/`` *is* an installed overlay
    (any dir, manifest or not). Returns sorted names; empty if the root is absent.
    """
    root = scope.overlay_root
    if not root.is_dir():
        return []
    return sorted(
        p.name for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")
    )


def filter_names(names: "list[str]", pattern: "Optional[str]") -> "list[str]":
    """Glob-filter overlay names (``sync 'py*'``). ``None``/``'*'`` keep all."""
    if not pattern or pattern == "*":
        return list(names)
    return [n for n in names if fnmatch.fnmatch(n, pattern)]


class OverlaySource:
    """A resolved place overlays are fetched *from* for ``add``/``sync``.

    ``root`` is a local directory holding ``<name>/`` overlay dirs. ``available()``
    lists them; ``overlay_dir(name)`` resolves one (raising if absent). This is the
    seam a future git/URI source slots into: a ``GitOverlaySource`` would clone/pull
    into a cache in ``__init__`` and set ``root`` to that checkout -- the command
    classes only ever touch this interface, so they need no change.
    """

    def __init__(self, root: Path):
        self.root = Path(root)

    def available(self) -> "list[str]":
        if not self.root.is_dir():
            return []
        return sorted(
            p.name
            for p in self.root.iterdir()
            if p.is_dir() and not p.name.startswith(".")
        )

    def overlay_dir(self, name: str) -> Path:
        candidate = self.root / name
        if not candidate.is_dir():
            raise SystemExit(
                "error: overlay %r not found in source %s (available: %s)"
                % (name, self.root, ", ".join(self.available()) or "none")
            )
        return candidate

    def __repr__(self) -> str:
        return "OverlaySource(%s)" % self.root


#: Environment override for the default overlay source (a local directory today;
#: a git/URI string once ``resolve_source`` grows that branch). Read, never printed.
SOURCE_ENV = "DOTAGENTS_OVERLAYS_SRC"


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


def resolve_source(source: "Optional[str]" = None) -> OverlaySource:
    """Resolve the overlay source: explicit ``--source`` / ``$DOTAGENTS_OVERLAYS_SRC``,
    else the bundled ``overlays/``.

    Today every branch yields a **local directory** ``OverlaySource``. The single
    extension point for a git/URI source is here: when ``raw`` looks like a URI (a
    later change), construct a ``GitOverlaySource`` instead -- callers are unaffected
    because they only use the returned object's ``available``/``overlay_dir``.
    """
    raw = source or os.environ.get(SOURCE_ENV) or None
    if raw:
        # (extension point) a URI/git ``raw`` would branch to a cached clone here.
        root = Path(raw).expanduser()
        if not root.is_dir():
            raise SystemExit("error: --source path is not a directory: %s" % raw)
        return OverlaySource(root)

    bundled = bundled_overlays_root()
    if bundled is None:
        raise SystemExit(
            "error: no overlay source. This build bundles no overlays; pass "
            "--source <dir> or set %s to a directory of overlays." % SOURCE_ENV
        )
    return OverlaySource(bundled)
