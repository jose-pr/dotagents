"""Contract A resolution for dotagents.

Preserves the precedence walk and filename resolution from the precursor `agentic`.
"""

from __future__ import annotations

from pathlib import Path

#: Level names the walk itself uses. An overlay's DIRECTORY NAME is its level
#: label, so an overlay named like one of these would collide with the per-level
#: name-dict keys (`{"project-root": ""}` would drop that overlay's `bin`);
#: `Overlay.is_valid_name` rejects them.
LEVEL_NAMES = frozenset({"default", "overlay", "system", "user", "project", "project-root"})


def get_file_paths(
    *names: str | dict[str, str],
    scope,
    include_missing: bool = False,
) -> list[tuple[str, Path, Path | None]]:
    """Resolve file paths across the precedence hierarchy (Contract A) for a
    :class:`~dotagents._scope.Scope` (``scope.files(*names)`` is the same call).

    Precedence order -- each of the scope's :attr:`~dotagents._scope.Scope.stores`
    in turn, its overlays first, then the store itself; finally the project root:
    1. system overlays, then system (`Scope.system_root`: `/etc/agents`)
    2. user-store overlays, then user (`Scope.user_root`)
    3. project overlays (`<project_root>/.agents/overlays/<name>/`), then project
       (`<project_root>/.agents`) -- a project scope only. Added 2026-09-09:
       `overlays add` installs into the PROJECT scope by default, and nothing
       consumed those overlays' bin/lib/env/cmds/CONTEXT.md before.
    4. project-root (`project_root`) -- a project scope only

    An overlay installed in more than one store under the same name is one
    overlay, the later store's: it SHADOWS the earlier copies entirely
    (:meth:`Overlay.installed`, the one discovery function), so its
    bin/lib/env/cmds/CONTEXT.md are the only ones that resolve -- not stacked.

    Each returned tuple is ``(level, path, root)``: for an overlay, ``level`` is
    the overlay's directory name and ``root`` its directory; for every other
    level ``root`` is ``None`` -- that is how callers tell overlays apart.
    """
    files: list[tuple[str, Path, Path | None]] = []

    def add_name_paths(
        location: Path, level: str, root: Path | None = None, is_overlay: bool = False
    ) -> None:
        for name in names:
            if isinstance(name, str):
                name_dict = {level: name}
            else:
                name_dict = name

            default = name_dict.get("default")
            if is_overlay:
                default = name_dict.get("overlay", default)

            template = name_dict.get(level, default)
            if not template:
                continue

            files.append((level, location / template, root))

    # No manifest of any kind is required for an overlay to count -- not
    # ``CONTEXT.md``, not ``overlay.toml`` (the old ``CONTEXT.md`` gate was a
    # precursor leftover that silently excluded EVERY real overlay, D84).
    overlays = scope.overlays  # shadowing already applied, store-stamped

    for store in scope.stores:
        for overlay in overlays:
            if overlay.store == store:
                add_name_paths(overlay.path, overlay.name, root=overlay.path, is_overlay=True)
        add_name_paths(store, scope.store_level(store))

    if not scope.global_scope:
        add_name_paths(scope.project_root, "project-root")

    if include_missing:
        return files
    return [(level, path, root) for level, path, root in files if path.exists()]
