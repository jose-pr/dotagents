"""Contract A resolution for dotagents.

Preserves the precedence walk and filename resolution from the precursor `agentic`.
"""

from __future__ import annotations

from pathlib import Path


def get_file_paths(
    *names: str | dict[str, str],
    agents_dir: Path,
    project_root: Path,
    global_scope: bool = False,
    include_missing: bool = False,
) -> list[tuple[str, Path, Path | None]]:
    """Resolve file paths across the precedence hierarchy (Contract A).

    Precedence order:
    1. overlays (in ~/.agents/overlays or equivalent)
    2. system (/etc/agents)
    3. user (agents_dir)
    4. project (project_root / .agents) -- skipped if global_scope
    5. project-root (project_root) -- skipped if global_scope
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

    # 1. Overlays
    #
    # An installed overlay is any directory under ``overlays/`` whose name is a
    # valid overlay name -- the EXACT rule ``_scope.discover_overlays`` uses (the
    # two must agree on what counts). No manifest of any kind is required -- not
    # ``CONTEXT.md``, not ``overlay.toml``. The old ``CONTEXT.md`` gate was a
    # precursor leftover (`agentic` used CONTEXT.md as an overlay manifest;
    # dotagents ships none) that silently excluded EVERY real dotagents overlay
    # from the Contract-A walk -- overlay-level bin/env/cmds resolution never fired
    # (D84). ``is_valid_overlay_name`` skips ``.git``/``__pycache__``/dotfiles.
    from dotagents._scope import is_valid_overlay_name

    overlay_root = agents_dir / "overlays"
    if overlay_root.is_dir():
        for overlay_dir in sorted(overlay_root.iterdir()):
            if overlay_dir.is_dir() and is_valid_overlay_name(overlay_dir.name):
                add_name_paths(
                    overlay_dir, overlay_dir.name, root=overlay_dir, is_overlay=True
                )

    # 2. System
    add_name_paths(Path("/etc/agents"), "system")

    # 3. User
    add_name_paths(agents_dir, "user")

    # 4 & 5. Project (if not global)
    if not global_scope:
        add_name_paths(project_root / ".agents", "project")
        add_name_paths(project_root, "project-root")

    if include_missing:
        return files
    return [(level, path, root) for level, path, root in files if path.exists()]
