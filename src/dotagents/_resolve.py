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

    Precedence order:
    1. user-store overlays (`<agents_dir>/overlays/<name>/`)
    2. system (/etc/agents)
    3. user (agents_dir)
    4. project overlays (`<project_root>/.agents/overlays/<name>/`) -- skipped if
       global_scope. Added 2026-09-09: `overlays add` installs into the PROJECT
       scope by default, and nothing consumed those overlays' bin/lib/env/cmds/
       CONTEXT.md before -- only the AGENTS.md recompose saw them.
    5. project (project_root / .agents) -- skipped if global_scope
    6. project-root (project_root) -- skipped if global_scope

    An overlay installed in BOTH scopes under the same name is one overlay, the
    project's: it SHADOWS the store's copy entirely (:meth:`Overlay.installed`,
    the one discovery function), so its bin/lib/env/cmds/CONTEXT.md are the only
    ones that resolve -- not both copies stacked.

    Each returned tuple is ``(level, path, root)``: for an overlay, ``level`` is
    the overlay's directory name and ``root`` its directory; for every other
    level ``root`` is ``None`` -- that is how callers tell overlays apart.
    """
    agents_dir = scope.user_root
    project_root = scope.project_root
    global_scope = scope.global_scope
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
    overlays = scope.overlays

    # 1. User-store overlays (minus the ones a same-named project overlay shadows)
    for overlay in overlays:
        if overlay.store == scope.user_root:
            add_name_paths(overlay.path, overlay.name, root=overlay.path, is_overlay=True)

    # 2. System
    add_name_paths(Path("/etc/agents"), "system")

    # 3. User
    add_name_paths(agents_dir, "user")

    # 4, 5 & 6. Project (if not global)
    if not global_scope:
        for overlay in overlays:
            if overlay.store == scope.project_store:
                add_name_paths(overlay.path, overlay.name, root=overlay.path, is_overlay=True)
        add_name_paths(scope.project_store, "project")
        add_name_paths(project_root, "project-root")

    if include_missing:
        return files
    return [(level, path, root) for level, path, root in files if path.exists()]
