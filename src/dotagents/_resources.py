"""The package's own data, reachable from a plain install and from a ``.pyz``.

Everything here is library code: the CLI and the helper modules both import
it, and it imports nothing from ``dotagents.cli``.

* :data:`BASE_ROOT` -- the bundled base overlay (``_overlay/``): the block
  templates, the bundled command modules and the harness hook scripts.
* :func:`base_agents_text` -- a base overlay's ``AGENTS.md`` block rendered
  for one store.
* ``_package_data_dir`` / ``_scratch_dir`` -- a package-data directory as a
  real filesystem path (extracted once from a zipapp), and the one per-process
  temp directory such extractions go to.
"""

from __future__ import annotations

import importlib.resources
import os
import shutil
import tempfile
from pathlib import Path

_extracted_dirs_cache: "dict[str, Path]" = {}
_scratch: "Path | None" = None


def _scratch_dir() -> Path:
    """The ONE per-process temp directory for anything extracted out of a
    zipapp (package data, the repointed module sources) and for `launch`'s
    context file, created lazily on first use and removed at interpreter exit."""
    global _scratch
    if _scratch is None:
        import atexit

        _scratch = Path(tempfile.mkdtemp(prefix="dotagents-"))
        atexit.register(shutil.rmtree, str(_scratch), True)
    return _scratch


def pyz_archive() -> "str | None":
    """The ``.pyz`` this dotagents is imported from, or None for a plain
    install. Inside a zipapp a module's ``__file__`` is a path INTO the
    archive (``.../dotagents.pyz/dotagents/_resources.py``), so the archive
    is the ancestor that is a file. It also holds the vendored runtime
    dependencies (duho, pathlib_next), importable from it by zipimport."""
    for parent in Path(__file__).parents:
        if parent.is_file():
            return str(parent)
    return None


def _package_data_dir(name: str) -> "Path | None":
    """Resolve a directory under the installed `dotagents` package (e.g.
    `_overlay`) to a real filesystem Path, working whether the package is a
    plain directory (pip install / editable) or inside a zipapp.

    Inside a zipapp, `importlib.resources.files()` returns a `zipfile.Path`
    (a `Traversable`, not a real filesystem `Path`): `.is_dir()` correctly
    reports membership in the archive, but `str(traversable)` produces a
    path string that does not exist on disk (`Path(str(...)).exists()` is
    always False -- there is no real file there to stat). So a zip-backed
    hit is extracted once to a process-lifetime temp directory and that
    real path is cached and returned; a plain-directory hit is returned
    as-is. Returns None if the directory isn't present in the package at all.
    """
    if name in _extracted_dirs_cache:
        return _extracted_dirs_cache[name]

    traversable = importlib.resources.files("dotagents") / name
    if not traversable.is_dir():
        return None

    as_path = Path(str(traversable))
    if as_path.exists():
        _extracted_dirs_cache[name] = as_path
        return as_path

    # Zip-backed (or otherwise non-filesystem) Traversable: extract under the
    # ONE process scratch dir, removed at exit (see `_scratch_dir`).
    extract_root = _scratch_dir() / name
    if extract_root.exists():
        shutil.rmtree(str(extract_root), ignore_errors=True)

    def _extract(node, dest: Path):
        dest.mkdir(parents=True, exist_ok=True)
        for child in node.iterdir():
            child_dest = dest / child.name
            if child.is_dir():
                _extract(child, child_dest)
            else:
                child_dest.write_bytes(child.read_bytes())

    _extract(traversable, extract_root)
    _extracted_dirs_cache[name] = extract_root
    return extract_root


# The base overlay (neutral minimum) is bundled package data at
# `src/dotagents/_overlay`. `init` renders its block template into the store's
# `AGENTS.md` and copies nothing else into the store: the bundled command
# modules are discovered from the package, and the hook scripts are deployed
# into an agent's own config dir. Overlays beyond the base are opt-in and
# installed by name with `overlays add` from a source dir (`--repo` /
# `$AGENTS_OVERLAYS_REPO`; this package bundles none of them).
BASE_ROOT = _package_data_dir("_overlay") or (Path(__file__).resolve().parent / "_overlay")

#: Where the base AGENTS.md block template lives inside a base overlay dir.
BASE_AGENTS_TEMPLATE = "dotagents/templates/AGENTS.md"
#: The block template for a PROJECT store: only what the project's overlays
#: add. The user store's block already carries the always-on rules, and every
#: session reads both, so a project copy of them was loaded twice.
BASE_PROJECT_TEMPLATE = "dotagents/templates/PROJECT.md"
#: Rendered by `base_agents_text` as the actual path of the store's AGENTS.md.
AGENTS_MD_PLACEHOLDER = "{{AGENTS_MD}}"


def base_agents_text(
    src: "str | os.PathLike[str]", dest: "str | os.PathLike[str]", *, project: bool = False
) -> str:
    """The base AGENTS.md block for the store at ``dest``: the template
    (``<src>/dotagents/templates/AGENTS.md``, or ``PROJECT.md`` for a project
    store when the base has one; falling back to ``<src>/AGENTS.md`` for a
    ``--from`` base that keeps it at its root) with ``{{AGENTS_MD}}`` rendered
    as the ACTUAL path of the file being written, so the block's "annotate that
    you read `…`" line names this store's file."""
    src_path = Path(src)
    template = src_path / BASE_AGENTS_TEMPLATE
    if project and (src_path / BASE_PROJECT_TEMPLATE).is_file():
        template = src_path / BASE_PROJECT_TEMPLATE
    elif not template.is_file():
        template = src_path / "AGENTS.md"
    text = template.read_text(encoding="utf-8")
    agents_md = (Path(dest).expanduser().resolve() / "AGENTS.md").as_posix()
    return text.replace(AGENTS_MD_PLACEHOLDER, agents_md)
