"""Assemble the effective context an agent should see in a scope."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, Optional

from dotagents import _agents
from dotagents import _env
from dotagents import _overlays
from dotagents import _scope


#: A ``$NAME`` / ``${NAME}`` shell-variable reference.
_SHELL_VAR = re.compile(r'\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*)(?![A-Za-z0-9_]))')


def _expand_placeholders(
    text: str, project_root: Path, overlay_roots: "list[Path]", *, env_vars: bool = False
) -> str:
    """Expand <PROJECT_ROOT> and <OVERLAY_NAME_OVERLAY_ROOT> placeholders.

    The placeholder name is exactly the env var ``dotagents env`` emits for the
    same overlay (:attr:`_overlays.Overlay.root_var`), so a context file and an
    env file refer to an overlay's install dir by one name.

    ``env_vars=True`` also expands ``$<NAME>_OVERLAY_ROOT`` /
    ``${<NAME>_OVERLAY_ROOT}`` for every installed overlay -- the form overlay
    routing lines use, which only a shell that ``dotagents env`` populated can
    resolve. Only for output that is handed over and discarded (stdout, JSON,
    ``launch``); a file that may be committed keeps the variable, since the
    expansion is a machine path."""
    text = text.replace("<PROJECT_ROOT>", str(project_root))
    roots = {_overlays.Overlay(ov).root_var: str(ov) for ov in overlay_roots}
    for var, value in roots.items():
        text = text.replace("<%s>" % var, value)
    if env_vars and roots:
        text = _SHELL_VAR.sub(
            lambda m: roots.get(m.group(1) or m.group(2), m.group(0)), text
        )
    return text


# A relative path token ending in .md: one or more path segments, no spaces,
# no leading slash or `~` (absolute paths are already-known files, not on-demand
# pointers), at least the trailing `.md`. Used for the BARE reference pass. Not
# after `$` either: `$FOO/x.md` is a variable's path, not a relative one.
_BARE_MD_REF = re.compile(r'(?<![\w`./~$-])((?:[\w.-]+/)*[\w.-]+\.md)\b')

#: A reference through a root placeholder or variable: ``$NAME_OVERLAY_ROOT/x.md``,
#: ``${NAME_OVERLAY_ROOT}/x.md``, ``<NAME_OVERLAY_ROOT>/x.md``, ``<PROJECT_ROOT>/x.md``.
_VAR_MD_REF = re.compile(
    r'(?:\$\{(?P<braced>[A-Z0-9_]+)\}|\$(?P<bare>[A-Z0-9_]+)|<(?P<angle>[A-Z0-9_]+)>)'
    r'/(?P<rel>(?:[\w.-]+/)*[\w.-]+\.md)\b'
)

#: Bare filenames (no directory) that name a harness-walked context file rather
#: than an on-demand target -- and would match every mention of the word.
_HARNESS_FILENAMES = frozenset({
    "AGENTS.md", "AGENTS.local.md", "CONTEXT.md", "CLAUDE.md", "CLAUDE.local.md",
    "GEMINI.md",
})

#: The `<!-- Source: ... -->` provenance comments this module emits.
_SOURCE_COMMENT = re.compile(r'(?m)^<!-- Source:.*?-->\s*$')


def _find_var_refs(text: str) -> "list[tuple[str, str, str, str]]":
    """References through a root variable or placeholder, as
    ``(ref-as-written, form, NAME, relative path)`` with ``form`` ``"$"`` or
    ``"<>"``. Order-preserving, de-duplicated."""
    seen: "dict[str, tuple[str, str, str, str]]" = {}
    for m in _VAR_MD_REF.finditer(_SOURCE_COMMENT.sub("", text)):
        name = m.group("braced") or m.group("bare") or m.group("angle")
        form = "<>" if m.group("angle") else "$"
        seen.setdefault(m.group(0), (m.group(0), form, name, m.group("rel")))
    return list(seen.values())


def _find_md_refs(text: str) -> "list[str]":
    """Collect on-demand markdown references, both backticked and bare.

    An `AGENTS.md` says "read kb/X.md before Y" as often bare as backticked, so
    both forms are matched. Excluded:
    - the `<!-- Source: ... -->` provenance comments this module emits (they are
      absolute source paths, not on-demand pointers),
    - absolute / home paths (already-loaded, not on-demand),
    - references through a root variable or placeholder (:func:`_find_var_refs`
      resolves those),
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

    # Drop the provenance comments and the variable references before scanning
    # so neither is re-read as a relative reference.
    scannable = _VAR_MD_REF.sub("", _SOURCE_COMMENT.sub("", text))

    # Backticked refs first (highest confidence).
    for m in re.findall(r'`([^`]+?\.md)`', scannable):
        if not m.startswith(("/", "~", "$", "<")):
            _add(m)
    # Bare refs.
    for m in _BARE_MD_REF.findall(scannable):
        _add(m)
    return list(seen)


def _source_search_roots(
    scope: _scope.Scope, level: str, path: Path, root: "Optional[Path]"
) -> "list[Path]":
    """Where a source's relative references resolve: the source's own
    directory -- the overlay root, or the store it sits in -- and, for the
    project's own sources only, the rest of the project. A user or system
    source never resolves into the project (or a cwd repo under ``-g``)."""
    if root is not None:
        return [root]
    roots = [path.parent]
    if level in ("project", "project-root"):
        for extra in (scope.project_store, scope.project_root):
            if extra is not None and extra not in roots:
                roots.append(extra)
    return roots


def _inline_referenced_files(
    text: str,
    sources: "list[tuple[str, str, list[Path]]]",
    *,
    var_roots: "dict[str, Path]",
    allowed_roots: "list[Path]",
    exclude: "Iterable[Path]" = (),
) -> str:
    """Find markdown file references (backticked, bare, or through a root
    variable) in each source and append the files they name.

    This defeats unreliable on-demand loading: an `AGENTS.md` that merely points
    at `kb/X.md` gets that file's content appended inline so the agent never has
    to fetch it. ``sources`` is ``(owner, content, search_roots)`` per emitted
    source: a relative reference resolves against THAT source's roots
    (:func:`_source_search_roots`), so an overlay's ``kb/X.md`` is the
    overlay's own file, never a same-named one in the store or the project. A
    ``$NAME_OVERLAY_ROOT/...`` / ``<NAME_OVERLAY_ROOT>/...`` /
    ``<PROJECT_ROOT>/...`` reference resolves through ``var_roots`` and is kept
    only when it lands under an installed overlay or store
    (``allowed_roots``) or under the source's own roots. Unresolved references
    are left as-is, and a file in ``exclude`` (a source already emitted, or one
    the harness loads itself) is never inlined twice.

    OPT-IN (``inline=True`` on the assemblers / ``context --inline``): the base
    AGENTS.md's own rule is "read the matching file BEFORE such a task; skip it
    otherwise, never preemptively", and inlining every mention does the
    opposite."""
    excluded = {_scope._path_key(p) for p in exclude}

    #: resolved-path key -> (heading, file)
    inlined: "dict[str, tuple[str, Path]]" = {}
    headings: "set[str]" = set()

    def _take(ref: str, owner: str, cand: Path) -> bool:
        try:
            if not cand.is_file():
                return False
        except OSError:
            return False
        key = _scope._path_key(cand)
        if key in excluded or key in inlined:
            return True
        heading = ref if ref not in headings else "%s (%s)" % (ref, owner)
        headings.add(heading)
        inlined[key] = (heading, cand)
        return True

    for owner, content, roots in sources:
        for ref, form, name, rel in _find_var_refs(content):
            if name == "PROJECT_ROOT":
                base = var_roots.get(name) if form == "<>" else None
            else:
                base = var_roots.get(name)
            if base is None:
                continue
            cand = base / rel
            if any(_scope._is_within(cand, r) for r in [*allowed_roots, *roots]):
                _take(ref, owner, cand)
        for ref in _find_md_refs(content):
            for root in roots:
                if _take(ref, owner, root / ref):
                    break

    if inlined:
        appends = ["\n\n## On-Demand Files (Inlined)\n"]
        for heading, path in inlined.values():
            try:
                body = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            appends.append("### %s\n\n%s\n" % (heading, body))
        return text + "\n".join(appends)
    return text


#: A YAML block-scalar indicator: `>`, `|`, optionally with chomping/indent.
_BLOCK_SCALAR = re.compile(r'^[>|][+-]?[0-9]?[+-]?$')


def _frontmatter(text: str) -> "dict[str, str]":
    """The top-level string keys of a leading ``---`` frontmatter block.

    Only the block at the very top counts (a ``name:`` line in the body is not
    metadata). A ``>`` / ``|`` block scalar and a plain value continued on
    indented lines are joined into one line -- the listing has one line per
    skill -- and matching surrounding quotes are dropped."""
    lines = text.lstrip("﻿").splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    out: "dict[str, str]" = {}
    key: "Optional[str]" = None
    parts: "list[str]" = []

    def _flush() -> None:
        if key is None:
            return
        value = " ".join(parts).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        out[key] = value

    for line in lines[1:]:
        if line.strip() in ("---", "..."):
            break
        m = re.match(r'^([A-Za-z_][\w-]*):(?:\s+(.*))?$', line)
        if m and not line[:1].isspace():
            _flush()
            key = m.group(1)
            value = (m.group(2) or "").strip()
            parts = [] if not value or _BLOCK_SCALAR.match(value) else [value]
        elif key is not None and line.strip() and line[:1].isspace():
            parts.append(line.strip())
    else:
        return {}  # never closed: not a frontmatter block
    _flush()
    return out


def _collect_skills(scope: _scope.Scope) -> "list[tuple[str, str, Path]]":
    """Discover available skills as ``(name, description, SKILL.md path)``.

    Skills are OPT-IN: the generator lists them so the user can choose to invoke
    one, but never inlines a skill body (that would defeat the user's
    'skills I decide to use' model). The ``skills/`` dirs are walked in
    contract-A order (:meth:`Scope.paths`: each store's overlays, then the
    store; system, user, project), and a LATER same-named skill wins -- a
    project skill over the user's, like :meth:`Overlay.installed`. Listed in
    first-seen order."""
    found: "dict[str, tuple[str, str, Path]]" = {}
    for _level, skills_dir, _root in scope.paths({"default": "skills", "project-root": ""}):
        if not skills_dir.is_dir():
            continue
        for skill_dir in sorted(skills_dir.iterdir()):
            skill_md = skill_dir / "SKILL.md"
            try:
                if not skill_md.is_file():
                    continue
                meta = _frontmatter(skill_md.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError):
                continue
            name, desc = meta.get("name"), meta.get("description")
            if name and desc:
                found[name] = (name, desc, skill_md)
    return list(found.values())


def _get_skills_listing(scope: _scope.Scope) -> str:
    """Formatted opt-in skills listing (markdown), or '' if none. Each entry
    names its ``SKILL.md``: a harness that does not scan the skills dirs itself
    has no other way to find it, and the directory need not match the name."""
    skills = _collect_skills(scope)
    if not skills:
        return ""
    lines = ["- **%s** (`%s`): %s" % (n, p, d) for n, d, p in skills]
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

    # Apply overlay priority: overlays first, in `Overlay.sort_key` order --
    # (priority, manifest name, directory name), the order `_compose_block`
    # folds overlay rules in -- so a lower priority reads earlier; then every
    # non-overlay level (system/user/project/project-root) in the resolver's
    # precedence order (the sort is stable). No priority value can move an
    # overlay after a store's AGENTS.md.
    #
    # An overlay entry is the one whose `root` is set: the resolver labels it
    # with the overlay's NAME, not the literal "overlay".
    def _sort_key(item):
        level, path, root = item
        if root is not None:
            return (0,) + _overlays.Overlay(root).sort_key
        return (1,)

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
    agent: _agents.Agent, scope: _scope.Scope, inline: bool, expand_vars: bool
) -> "tuple[str, list[str]]":
    """The shared body of both assemblers: ``(text, source_paths)``."""
    filtered_sources, harness_loaded = _resolve_and_filter_sources(agent, scope)
    project_root = scope.project_root or _scope.project_root_default()
    overlay_roots = _env.get_overlay_roots(scope)

    assembled_parts = []
    source_paths: "list[str]" = []
    emitted: "list[Path]" = []
    ref_sources: "list[tuple[str, str, list[Path]]]" = []

    for level, path, root in filtered_sources:
        try:
            content = path.read_text(encoding="utf-8")
        except OSError:
            continue
        source_paths.append(str(path))
        emitted.append(path)
        ref_sources.append((level, content, _source_search_roots(scope, level, path, root)))
        assembled_parts.append(f"<!-- Source: {path} -->\n{content.strip()}\n")

    text = "\n\n".join(assembled_parts)
    if inline and text:
        var_roots = {_overlays.Overlay(ov).root_var: ov for ov in overlay_roots}
        var_roots["PROJECT_ROOT"] = project_root
        text = _inline_referenced_files(
            text, ref_sources,
            var_roots=var_roots,
            allowed_roots=[*scope.stores, *overlay_roots],
            exclude=[*emitted, *harness_loaded],
        )
    # Placeholders last, so ones inside inlined files expand too; every
    # installed overlay gets a root, matching `dotagents env`.
    text = _expand_placeholders(text, project_root, overlay_roots, env_vars=expand_vars)
    return text, source_paths


def assemble_context(
    agent: _agents.Agent, scope: _scope.Scope, *, inline: bool = False,
    expand_vars: bool = True,
) -> str:
    """Assemble the effective context text (markdown) for the given agent in a
    :class:`~dotagents._scope.Scope`.

    Returns '' if, after subtracting what the agent's harness already loads,
    there is nothing new to emit (no empty double of already-loaded content).
    ``inline=True`` appends the on-demand files the sources reference (see
    :func:`_inline_referenced_files` for why that is opt-in).
    ``expand_vars`` (default on) replaces ``$<NAME>_OVERLAY_ROOT`` with the
    overlay's directory, for a reader with no populated shell; pass ``False``
    for text written into a file that may be committed (see
    :func:`_expand_placeholders`)."""
    text, source_paths = _assemble(agent, scope, inline, expand_vars)
    if not source_paths:
        return ""
    text += _get_skills_listing(scope)
    return text


def assemble_context_data(
    agent: _agents.Agent, scope: _scope.Scope, *, inline: bool = False,
    expand_vars: bool = True,
) -> "dict[str, object]":
    """Structured form of the assembled context, for ``--format json``.

    Shape:
        {
          "agent": <registry name>,
          "harness": <harness_id>,
          "sources": [<absolute source path>, ...],  # after harness subtraction
          "context": <assembled markdown text, minus the skills listing>,
          "skills": [{"name": ..., "description": ..., "path": <SKILL.md>}, ...],
        }
    ``context`` is the same assembled(+inlined, with ``inline=True``) text the
    markdown format emits, but WITHOUT the skills listing appended -- skills are
    their own structured field so a consumer can render them separately and
    keep the opt-in distinction."""
    text, source_paths = _assemble(agent, scope, inline, expand_vars)

    skills = [
        {"name": n, "description": d, "path": str(p)} for n, d, p in _collect_skills(scope)
    ]

    return {
        "agent": agent.name,
        "harness": agent.harness_id or agent.name,
        "sources": source_paths,
        "context": text,
        "skills": skills,
    }
