"""Assemble the effective context an agent should see in a scope."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, Optional

from dotagents import _agents
from dotagents import _overlays
from dotagents import _scope


def _expand_placeholders(text: str, project_root: Path, overlay_roots: list[Path]) -> str:
    """Expand <PROJECT_ROOT> and <OVERLAY_NAME_OVERLAY_ROOT> placeholders.

    The placeholder name is exactly the env var ``dotagents env`` emits for the
    same overlay (:attr:`_overlays.Overlay.root_var`), so a context file and an
    env file refer to an overlay's install dir by one name."""
    text = text.replace("<PROJECT_ROOT>", str(project_root))
    for ov in overlay_roots:
        text = text.replace(f"<{_overlays.Overlay(ov).root_var}>", str(ov))
    return text


def _overlay_roots(scope: _scope.Scope) -> "list[Path]":
    """Every installed overlay's dir (:attr:`Scope.overlays`: the user store's,
    then the project's, shadowing applied) -- the same set ``dotagents env``
    names with a ``<NAME>_OVERLAY_ROOT`` var, so a placeholder resolves for ANY
    installed overlay, not only one that happens to ship a ``CONTEXT.md``."""
    return [overlay.path for overlay in scope.overlays]


# A relative path token ending in .md: one or more path segments, no spaces,
# no leading slash or `~` (absolute paths are already-known files, not on-demand
# pointers), at least the trailing `.md`. Used for the BARE reference pass.
_BARE_MD_REF = re.compile(r'(?<![\w`./~-])((?:[\w.-]+/)*[\w.-]+\.md)\b')

#: Bare filenames (no directory) that name a harness-walked context file rather
#: than an on-demand target -- and would match every mention of the word.
_HARNESS_FILENAMES = frozenset({
    "AGENTS.md", "AGENTS.local.md", "CONTEXT.md", "CLAUDE.md", "CLAUDE.local.md",
    "GEMINI.md",
})


def _find_md_refs(text: str) -> "list[str]":
    """Collect on-demand markdown references, both backticked and bare.

    An `AGENTS.md` says "read kb/X.md before Y" as often bare as backticked, so
    both forms are matched. Excluded:
    - the `<!-- Source: ... -->` provenance comments this module emits (they are
      absolute source paths, not on-demand pointers),
    - absolute / home paths (already-loaded, not on-demand),
    - harness-walked context files (`AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, ...,
      bare or under `.agents/`) -- a source or a harness load, never an
      on-demand pointer, and inlining one double-sends it.
    Order-preserving, de-duplicated.
    """
    seen: "dict[str, None]" = {}

    def _add(ref: str) -> None:
        ref = ref.strip()
        if not ref or ref in seen:
            return
        if ref.rsplit("/", 1)[-1] in _HARNESS_FILENAMES and (
            "/" not in ref or ref.startswith((".agents/", ".claude/", ".gemini/"))
        ):
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


def _inline_referenced_files(
    text: str, search_roots: list[Path], exclude: "Iterable[Path]" = ()
) -> str:
    """Find markdown file references (backticked or bare) and inline them.

    This defeats unreliable on-demand loading: an `AGENTS.md` that merely points
    at `kb/X.md` gets that file's content appended inline so the agent never has
    to fetch it. Only references that resolve to a real file under a search root
    are inlined; unresolved references are left as-is, and a file in ``exclude``
    (a source already emitted, or one the harness loads itself) is never
    inlined twice.

    OPT-IN (``inline=True`` on the assemblers / ``context --inline``): the base
    AGENTS.md's own rule is "read the matching file BEFORE such a task; skip it
    otherwise, never preemptively", and inlining every mention does the
    opposite."""
    refs = _find_md_refs(text)
    excluded = set()
    for p in exclude:
        try:
            excluded.add(Path(p).resolve())
        except OSError:
            pass

    inlined: "dict[str, str]" = {}
    for ref in refs:
        for root in search_roots:
            cand = root / ref
            try:
                if cand.is_file():
                    if cand.resolve() in excluded:
                        break
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


def _collect_skills(scope: _scope.Scope) -> "list[tuple[str, str]]":
    """Discover available skills as (name, description) pairs.

    Skills are OPT-IN: the generator lists them so the user can choose to invoke
    one, but never inlines a skill body (that would defeat the user's
    'skills I decide to use' model). Deterministic order: the scope's stores
    (the user store, then the project's ``.agents``), then every installed
    overlay (:attr:`Scope.overlays` -- ``.git`` / ``__pycache__`` never count)."""
    roots = list(scope.stores) + _overlay_roots(scope)

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


def _get_skills_listing(scope: _scope.Scope) -> str:
    """Formatted opt-in skills listing (markdown), or '' if none."""
    skills = _collect_skills(scope)
    if not skills:
        return ""
    lines = ["- **%s**: %s" % (n, d) for n, d in skills]
    return "\n\n## Available Skills (Opt-in)\n" + "\n".join(lines) + "\n"


def _resolve_and_filter_sources(
    agent: _agents.Agent, scope: _scope.Scope
) -> "tuple[list[tuple[str, Path, Path | None]], list[Path]]":
    """Resolve context sources (contract A), priority-order them, and subtract
    what the active agent's harness already loads so nothing already in the
    harness's context is re-emitted (no double-send).

    Returns ``(sources, harness_loaded)`` -- the second is the resolved list of
    files the harness loads itself, so the inliner can skip them too."""
    sources = scope.paths(
        {"overlay": "CONTEXT.md", "default": "AGENTS.md"},
        {"project": "AGENTS.local.md", "project-root": "AGENTS.local.md"},
    )
    project_root = scope.project_root or _scope.project_root_default()

    # Apply overlay priority: overlays sort among themselves by their declared
    # priority (lower first, read from the manifest by `Overlay.priority`; the
    # unprioritized default is `DEFAULT_PRIORITY`, 500); non-overlay levels
    # (system/user/project) keep the resolver's precedence order, placed after
    # all overlays via a high sentinel. Stable sort preserves resolver order
    # within equal keys.
    #
    # An overlay entry is the one whose `root` is set: the resolver labels it
    # with the overlay's NAME, not the literal "overlay".
    _NON_OVERLAY_SENTINEL = 10_000

    def _sort_key(item):
        level, path, root = item
        if root is not None:
            return (_overlays.Overlay(root).priority, path.name)
        return (_NON_OVERLAY_SENTINEL, "")

    sources.sort(key=_sort_key)

    # Subtract harness loads (no double-send). `Agent.loaded_paths` resolves the
    # static `harness_loads` list (a relative entry, e.g. Codex's "AGENTS.md",
    # means "relative to the PROJECT ROOT"; `~/` and `/` forms are absolute)
    # and, for a harness with an include mechanism (Claude's `@path` lines),
    # whatever its entry files actually include -- so the subtraction reflects
    # what is loaded on THIS machine, not an assumption. Paths are compared
    # resolved, never by bare filename.
    harness_loads_resolved = agent.loaded_paths(project_root)

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
            filtered.append(item)
    return filtered, harness_loads_resolved


def _assemble(
    agent: _agents.Agent, scope: _scope.Scope, inline: bool
) -> "tuple[str, list[str]]":
    """The shared body of both assemblers: ``(text, source_paths)``."""
    filtered_sources, harness_loaded = _resolve_and_filter_sources(agent, scope)
    project_root = scope.project_root or _scope.project_root_default()

    assembled_parts = []
    search_roots = [project_root, scope.user_root]
    source_paths: "list[str]" = []
    emitted: "list[Path]" = []

    for level, path, root in filtered_sources:
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            continue
        source_paths.append(str(path))
        emitted.append(path)
        if root:
            search_roots.append(root)
        assembled_parts.append(f"<!-- Source: {path} -->\n{content.strip()}\n")

    text = "\n\n".join(assembled_parts)
    if inline and text:
        text = _inline_referenced_files(
            text, search_roots, exclude=[*emitted, *harness_loaded]
        )
    # Placeholders last, so ones inside inlined files expand too; every
    # installed overlay gets a root, matching `dotagents env`.
    text = _expand_placeholders(text, project_root, _overlay_roots(scope))
    return text, source_paths


def assemble_context(
    agent: _agents.Agent, scope: _scope.Scope, *, inline: bool = False
) -> str:
    """Assemble the effective context text (markdown) for the given agent in a
    :class:`~dotagents._scope.Scope`.

    Returns '' if, after subtracting what the agent's harness already loads,
    there is nothing new to emit (no empty double of already-loaded content).
    ``inline=True`` appends the on-demand files the sources reference (see
    :func:`_inline_referenced_files` for why that is opt-in)."""
    text, source_paths = _assemble(agent, scope, inline)
    if not source_paths:
        return ""
    text += _get_skills_listing(scope)
    return text


def assemble_context_data(
    agent: _agents.Agent, scope: _scope.Scope, *, inline: bool = False
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
    ``context`` is the same assembled(+inlined, with ``inline=True``) text the
    markdown format emits, but WITHOUT the skills listing appended -- skills are
    their own structured field so a consumer can render them separately and
    keep the opt-in distinction."""
    text, source_paths = _assemble(agent, scope, inline)

    skills = [{"name": n, "description": d} for n, d in _collect_skills(scope)]

    return {
        "agent": agent.name,
        "harness": agent.harness_id or agent.name,
        "sources": source_paths,
        "context": text,
        "skills": skills,
    }
