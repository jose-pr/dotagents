"""Assemble effective context (Plan 04)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from dotagents import _resolve
from dotagents import _agents
from dotagents import _overlays


def _get_overlay_priority(overlay_dir: Path) -> int:
    """Read priority from overlay.toml or default to 100."""
    manifest = _overlays.read_manifest(overlay_dir)
    # Note: Plan 02 is deferred, but we implement basic priority parsing
    # if it happens to be present in overlay.toml, or default to 100.
    path = overlay_dir / "overlay.toml"
    if path.is_file():
        try:
            content = path.read_text(encoding="utf-8")
            m = re.search(r'(?m)^priority\s*=\s*(\d+)', content)
            if m:
                return int(m.group(1))
        except OSError:
            pass
    return 100


def _expand_placeholders(text: str, project_root: Path, overlay_roots: list[Path]) -> str:
    """Expand <PROJECT_ROOT> and <OVERLAY_NAME_OVERLAY_ROOT> placeholders."""
    text = text.replace("<PROJECT_ROOT>", str(project_root))
    for ov in overlay_roots:
        env_name = ov.name.upper().replace("-", "_")
        text = text.replace(f"<{env_name}_OVERLAY_ROOT>", str(ov))
    return text


def _inline_referenced_files(text: str, search_roots: list[Path]) -> str:
    """Find markdown file references (e.g., `kb/PYTHON.md`) and inline them."""
    # Look for paths ending in .md inside backticks
    refs = set(re.findall(r'`([^`]+?\.md)`', text))
    
    inlined = {}
    for ref in refs:
        for root in search_roots:
            cand = root / ref
            if cand.is_file():
                inlined[ref] = cand.read_text(encoding="utf-8")
                break
                
    if inlined:
        appends = ["\n\n## On-Demand Files (Inlined)\n"]
        for ref, content in inlined.items():
            appends.append(f"### {ref}\n\n{content}\n")
        return text + "\n".join(appends)
    return text


def _get_skills_listing(agents_dir: Path, project_root: Path, global_scope: bool) -> str:
    """Discover skills and return a formatted listing."""
    roots = [agents_dir]
    if not global_scope:
        roots.append(project_root / ".agents")
        
    overlay_root = agents_dir / "overlays"
    if overlay_root.is_dir():
        roots.extend([d for d in overlay_root.iterdir() if d.is_dir()])
        
    skills = []
    for root in roots:
        skills_dir = root / "skills"
        if not skills_dir.is_dir():
            continue
        for skill_dir in skills_dir.iterdir():
            if not skill_dir.is_dir():
                continue
            skill_md = skill_dir / "SKILL.md"
            if not skill_md.is_file():
                continue
                
            try:
                content = skill_md.read_text(encoding="utf-8")
                name_match = re.search(r'(?m)^name:\s*(.+)$', content)
                desc_match = re.search(r'(?m)^description:\s*(.+)$', content)
                if name_match and desc_match:
                    skills.append(f"- **{name_match.group(1).strip()}**: {desc_match.group(1).strip()}")
            except OSError:
                pass
                
    if skills:
        return "\n\n## Available Skills (Opt-in)\n" + "\n".join(skills) + "\n"
    return ""


def assemble_context(
    agent: _agents.Agent,
    agents_dir: Path,
    project_root: Path,
    global_scope: bool = False
) -> str:
    """Assemble the effective context for the given agent."""
    sources = _resolve.get_file_paths(
        {"overlay": "CONTEXT.md", "default": "AGENTS.md"},
        {"project": "AGENTS.local.md", "project-root": "AGENTS.local.md"},
        agents_dir=agents_dir,
        project_root=project_root,
        global_scope=global_scope,
        include_missing=False
    )
    
    # Apply overlay priority
    def _sort_key(item):
        level, path, root = item
        if level == "overlay" and root:
            return (_get_overlay_priority(root), path.name)
        return (200, path.name)
    
    sources.sort(key=_sort_key)
    
    # Subtract harness loads
    filtered_sources = []
    harness_loads_resolved = []
    for hl in agent.harness_loads:
        if hl.startswith("~/") or hl.startswith("/"):
            harness_loads_resolved.append(Path(hl).expanduser().resolve())
            
    for item in sources:
        level, path, root = item
        skip = False
        
        # Check exact resolved path
        try:
            resolved_path = path.resolve()
            if resolved_path in harness_loads_resolved:
                skip = True
        except OSError:
            pass
            
        # Check by name
        if not skip:
            for hl in agent.harness_loads:
                if not (hl.startswith("~/") or hl.startswith("/")) and path.name == hl:
                    skip = True
                    break
                    
        if not skip:
            filtered_sources.append(item)
            
    if not filtered_sources:
        return ""
        
    # Assemble text
    assembled_parts = []
    search_roots = [project_root, agents_dir]
    overlay_roots = []
    
    for level, path, root in filtered_sources:
        try:
            content = path.read_text(encoding="utf-8")
            if root:
                search_roots.append(root)
                if level == "overlay":
                    overlay_roots.append(root)
            assembled_parts.append(f"<!-- Source: {path} -->\n{content.strip()}\n")
        except OSError:
            pass
            
    text = "\n\n".join(assembled_parts)
    text = _expand_placeholders(text, project_root, overlay_roots)
    text = _inline_referenced_files(text, search_roots)
    text += _get_skills_listing(agents_dir, project_root, global_scope)
    
    return text
