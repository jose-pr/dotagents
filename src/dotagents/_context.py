"""Assemble effective context (Plan 04)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from dotagents import _resolve
from dotagents import _agents
from dotagents import _overlays


def _get_overlay_priority(overlay_dir: Path) -> int:
    """Overlay merge priority (plan 02), read from the manifest.

    The manifest reader owns priority parsing (`_overlays.read_manifest`), so
    this just consumes its value -- no duplicate ad-hoc regex. Lower sorts
    earlier; the unprioritized default is `_overlays.DEFAULT_PRIORITY` (500)."""
    manifest = _overlays.read_manifest(overlay_dir)
    value = manifest.get("priority", _overlays.DEFAULT_PRIORITY)
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return _overlays.DEFAULT_PRIORITY


def _expand_placeholders(text: str, project_root: Path, overlay_roots: list[Path]) -> str:
    """Expand <PROJECT_ROOT> and <OVERLAY_NAME_OVERLAY_ROOT> placeholders."""
    text = text.replace("<PROJECT_ROOT>", str(project_root))
    for ov in overlay_roots:
        env_name = ov.name.upper().replace("-", "_")
        text = text.replace(f"<{env_name}_OVERLAY_ROOT>", str(ov))
    return text


# A relative path token ending in .md: one or more path segments, no spaces,
# no leading slash or `~` (absolute paths are already-known files, not on-demand
# pointers), at least the trailing `.md`. Used for the BARE reference pass.
_BARE_MD_REF = re.compile(r'(?<![\w`./~-])((?:[\w.-]+/)*[\w.-]+\.md)\b')


def _find_md_refs(text: str) -> "list[str]":
    """Collect on-demand markdown references, both backticked and bare.

    An `AGENTS.md` says "read kb/X.md before Y" as often bare as backticked, so
    matching only backticks (the original bug) missed the very files the
    generator exists to inline. This catches both. Excluded:
    - the `<!-- Source: ... -->` provenance comments this module emits (they are
      absolute source paths, not on-demand pointers),
    - absolute / home paths (already-loaded, not on-demand),
    - the bare filename `AGENTS.md` with no directory (a harness-walked file, not
      an on-demand pointer -- and it would match every mention of the word).
    Order-preserving, de-duplicated.
    """
    seen: "dict[str, None]" = {}

    def _add(ref: str) -> None:
        ref = ref.strip()
        if not ref or ref in seen:
            return
        # Skip bare top-level filenames with no directory component that name a
        # harness-walked context file rather than an on-demand target.
        if "/" not in ref and ref in ("AGENTS.md", "AGENTS.local.md", "CONTEXT.md"):
            return
        seen[ref] = None

    # Drop the provenance comments before scanning so their absolute paths don't
    # get re-read as references.
    scannable = re.sub(r'(?m)^<!-- Source:.*?-->\s*$', '', text)

    # Backticked refs first (highest confidence).
    for m in re.findall(r'`([^`]+?\.md)`', scannable):
        if not m.startswith(("/", "~")):
            _add(m)
    # Bare refs.
    for m in _BARE_MD_REF.findall(scannable):
        _add(m)
    return list(seen)


def _inline_referenced_files(text: str, search_roots: list[Path]) -> str:
    """Find markdown file references (backticked or bare) and inline them.

    This defeats unreliable on-demand loading: an `AGENTS.md` that merely points
    at `kb/X.md` gets that file's content appended inline so the agent never has
    to fetch it. Only references that resolve to a real file under a search root
    are inlined; unresolved references are left as-is."""
    refs = _find_md_refs(text)

    inlined: "dict[str, str]" = {}
    for ref in refs:
        for root in search_roots:
            cand = root / ref
            try:
                if cand.is_file():
                    inlined[ref] = cand.read_text(encoding="utf-8")
                    break
            except OSError:
                pass

    if inlined:
        appends = ["\n\n## On-Demand Files (Inlined)\n"]
        for ref, content in inlined.items():
            appends.append(f"### {ref}\n\n{content}\n")
        return text + "\n".join(appends)
    return text


def _collect_skills(agents_dir: Path, project_root: Path, global_scope: bool) -> "list[tuple[str, str]]":
    """Discover available skills as (name, description) pairs.

    Skills are OPT-IN: the generator lists them so the user can choose to invoke
    one, but never inlines a skill body (that would defeat the user's
    'skills I decide to use' model). Deterministic order."""
    roots = [agents_dir]
    if not global_scope:
        roots.append(project_root / ".agents")

    overlay_root = agents_dir / "overlays"
    if overlay_root.is_dir():
        roots.extend([d for d in sorted(overlay_root.iterdir()) if d.is_dir()])

    skills: "list[tuple[str, str]]" = []
    seen: "set[str]" = set()
    for root in roots:
        skills_dir = root / "skills"
        if not skills_dir.is_dir():
            continue
        for skill_dir in sorted(skills_dir.iterdir()):
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
                    name = name_match.group(1).strip()
                    if name in seen:
                        continue
                    seen.add(name)
                    skills.append((name, desc_match.group(1).strip()))
            except OSError:
                pass
    return skills


def _get_skills_listing(agents_dir: Path, project_root: Path, global_scope: bool) -> str:
    """Formatted opt-in skills listing (markdown), or '' if none."""
    skills = _collect_skills(agents_dir, project_root, global_scope)
    if not skills:
        return ""
    lines = ["- **%s**: %s" % (n, d) for n, d in skills]
    return "\n\n## Available Skills (Opt-in)\n" + "\n".join(lines) + "\n"


def _resolve_and_filter_sources(
    agent: _agents.Agent,
    agents_dir: Path,
    project_root: Path,
    global_scope: bool,
) -> "list[tuple[str, Path, Path | None]]":
    """Resolve context sources (contract A), priority-order them, and subtract
    the active agent's ``harness_loads`` so nothing already in the harness's
    context is re-emitted (no double-send)."""
    sources = _resolve.get_file_paths(
        {"overlay": "CONTEXT.md", "default": "AGENTS.md"},
        {"project": "AGENTS.local.md", "project-root": "AGENTS.local.md"},
        agents_dir=agents_dir,
        project_root=project_root,
        global_scope=global_scope,
        include_missing=False,
    )

    # Apply overlay priority (plan 02): overlays sort among themselves by their
    # declared priority (lower first); non-overlay levels (system/user/project)
    # keep the resolver's precedence order, placed after all overlays via a high
    # sentinel. Stable sort preserves resolver order within equal keys.
    _NON_OVERLAY_SENTINEL = 10_000

    def _sort_key(item):
        level, path, root = item
        if level == "overlay" and root:
            return (_get_overlay_priority(root), path.name)
        return (_NON_OVERLAY_SENTINEL, "")

    sources.sort(key=_sort_key)

    # Subtract harness loads (no double-send).
    harness_loads_resolved = []
    for hl in agent.harness_loads:
        if hl.startswith("~/") or hl.startswith("/"):
            harness_loads_resolved.append(Path(hl).expanduser().resolve())

    filtered = []
    for item in sources:
        level, path, root = item
        skip = False
        try:
            if path.resolve() in harness_loads_resolved:
                skip = True
        except OSError:
            pass
        if not skip:
            for hl in agent.harness_loads:
                if not (hl.startswith("~/") or hl.startswith("/")) and path.name == hl:
                    skip = True
                    break
        if not skip:
            filtered.append(item)
    return filtered


def assemble_context(
    agent: _agents.Agent,
    agents_dir: Path,
    project_root: Path,
    global_scope: bool = False,
) -> str:
    """Assemble the effective context text (markdown) for the given agent.

    Returns '' if, after subtracting what the agent's harness already loads,
    there is nothing new to emit (no empty double of already-loaded content)."""
    filtered_sources = _resolve_and_filter_sources(
        agent, agents_dir, project_root, global_scope
    )
    if not filtered_sources:
        return ""

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


def assemble_context_data(
    agent: _agents.Agent,
    agents_dir: Path,
    project_root: Path,
    global_scope: bool = False,
) -> "dict[str, object]":
    """Structured form of the assembled context, for ``--format json``.

    Shape:
        {
          "agent": <registry name>,
          "harness": <harness_id>,
          "sources": [<absolute source path>, ...],  # after harness subtraction
          "context": <assembled markdown text, minus the skills listing>,
          "skills": [{"name": ..., "description": ...}, ...],  # opt-in listing
        }
    ``context`` is the same assembled+inlined text the markdown format emits, but
    WITHOUT the skills listing appended -- skills are their own structured field
    so a consumer can render them separately and keep the opt-in distinction."""
    filtered_sources = _resolve_and_filter_sources(
        agent, agents_dir, project_root, global_scope
    )

    assembled_parts = []
    search_roots = [project_root, agents_dir]
    overlay_roots = []
    source_paths: "list[str]" = []

    for level, path, root in filtered_sources:
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            continue
        source_paths.append(str(path))
        if root:
            search_roots.append(root)
            if level == "overlay":
                overlay_roots.append(root)
        assembled_parts.append(f"<!-- Source: {path} -->\n{content.strip()}\n")

    text = "\n\n".join(assembled_parts)
    text = _expand_placeholders(text, project_root, overlay_roots)
    text = _inline_referenced_files(text, search_roots)

    skills = [
        {"name": n, "description": d}
        for n, d in _collect_skills(agents_dir, project_root, global_scope)
    ]

    return {
        "agent": agent.name,
        "harness": agent.harness_id or agent.name,
        "sources": source_paths,
        "context": text,
        "skills": skills,
    }
