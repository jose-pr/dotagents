"""Guard against internal working-artefact labels leaking into tracked files.

This repo's own working notes (a numbered plan, a phase/item inside one, a
work-item code, a review writeup dated and cited by day) live in the private,
untracked ``.agents/`` directory and must never show up in anything this
repository ships -- not source, not a comment or docstring, not a test, not
the changelog. Those labels are meaningful only to someone with that private
directory open; to everyone else (a user reading ``--help``, a contributor
reading a diff, a future maintainer years later) they are noise at best and a
dangling reference at worst.

**This repo is a special case.** Managing ``.agents/``, plans, findings and
decision logs *is* dotagents' product, so code, docs and tests that talk
about those as product features (a ``findings`` queue, an install path under
``~/.agents``, a decision-log id cited by convention, ``INDEX.md``) are
legitimate and are deliberately NOT scanned here -- ``tools/audit.py`` and the
project's own ``leak-check`` override (``.agents/dotagents/cmds/leak_check.py``)
already own that distinction, with the domain-aware exemptions it requires.
This test covers the other axis: labels that are never a dotagents product
concept under any reading -- a numbered plan/phase/item, a work-item code, or
a review cited by its date -- which are always internal-process leakage here,
exactly as in any other repo.

One exception has its own dedicated test below: decision ids (``D84``,
``[[D22]]``) are cited by convention throughout this repo's shipped notes and
code comments (an explicit, written repo rule -- see ``.agents/AGENTS.md``),
so the ban on them is scoped to ``CHANGELOG.md`` only, which is released
history and names no implementation detail by citing one.

This scans every ``git``-tracked text file for the label shapes and fails
naming every offending ``path:line``. A short, explicit allowlist covers the
rare genuine domain use that happens to match (each entry carries its own
one-line reason). Skipped entirely outside a git checkout (e.g. a built
sdist/wheel), since there is no ``git ls-files`` to run there.

The matching logic is exercised directly (not just against this repo's
current, clean state) by a second test that feeds it a planted offender per
label shape and asserts it is caught -- so a future edit that loosens a
pattern shows up as a test failure here, not as a silent gap. Those planted
strings are assembled from pieces at runtime rather than written literally,
so this file's own source never contains the shapes it is built to catch.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_THIS_FILE = Path(__file__).resolve()

# --------------------------------------------------------------------------
# Label shapes
# --------------------------------------------------------------------------
# Each entry is (name, compiled pattern). Kept as data, not inlined into the
# scanner, so the planted-label test below can drive every shape generically.
#
# Deliberately NOT here: ``.agents`` path mentions and decision ids
# (``D\d{2}``) -- both are dotagents product concepts in this repo (the store
# path, the design log), already handled by `tools/audit.py` / `leak-check`
# with the domain-aware exemptions that distinction needs. Decision ids get
# their own narrower, CHANGELOG-scoped test below instead of a pattern here.

_PATTERNS = [
    ("numbered-plan", re.compile(r"\b[Pp]lan[ _-]?\d")),
    ("numbered-phase", re.compile(r"\b[Pp]hase[ _-]?\d")),
    ("numbered-item", re.compile(r"\b[Ii]tem[ _-]?\d")),
    ("task-range", re.compile(r"\bT\d+-T\d+\b")),
    (
        "work-item-code-paren",
        re.compile(r"\((?:F|G|M|H|L|S|C|R|B|P|T)-?\d{1,3}\)"),
    ),
    (
        "work-item-code-bold",
        re.compile(r"\*\*(?:F|G|M|H|L|S|C|R|B|P|T)\d{1,3}\*\*"),
    ),
    ("work-item-code-bare", re.compile(r"\b[FP]\d\b")),
    ("reviewer-reference", re.compile(r"\breviewer", re.IGNORECASE)),
    ("in-depth-review", re.compile(r"in-depth review", re.IGNORECASE)),
    ("review-finding", re.compile(r"review finding", re.IGNORECASE)),
    (
        "dated-review-reference",
        re.compile(
            r"\breview[ ,]+\d{4}-\d{2}-\d{2}\b|\b\d{4}-\d{2}-\d{2}[ ,]+review\b",
            re.IGNORECASE,
        ),
    ),
    ("backlog-item", re.compile(r"\bbacklog\b", re.IGNORECASE)),
]

# (path, substring, reason) -- a match on `path` whose offending line
# contains `substring` is a genuine domain use, not an internal-artefact
# reference.
_ALLOWLIST = [
    (
        "tests/test_agents.py",
        'AGENTS_AGENT": "reviewer"',
        "`reviewer` here is a value of dotagents' own AGENTS_AGENT persona "
        "variable (an agent-identity feature), not a reference to a human "
        "reviewer or this repo's review process",
    ),
]

# Decision ids are cited by convention everywhere EXCEPT the changelog (a
# written repo rule -- see .agents/AGENTS.md, "the repo's own rule scopes the
# ban to CHANGELOG.md only"). Released history should describe what shipped,
# not point a reader at a private design-log entry they cannot open.
_DECISION_ID_RE = re.compile(r"\[\[D\d{2}\]\]|\bD\d{2}\b")
_CHANGELOG_PATH = "CHANGELOG.md"


def _is_allowed(path: str, line: str) -> bool:
    return any(
        path == allowed_path and substring in line
        for allowed_path, substring, _reason in _ALLOWLIST
    )


def _scan_text(text: str):
    """Yield (pattern_name, line_no, line) for every label match in `text`."""
    for line_no, line in enumerate(text.splitlines(), start=1):
        for name, pattern in _PATTERNS:
            if pattern.search(line):
                yield name, line_no, line


def _git_tracked_files():
    try:
        result = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=_REPO_ROOT,
            capture_output=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    # NUL-separated (-z): plain output C-quotes and octal-escapes any path
    # with a non-ASCII byte, which then cannot be opened.
    return [p for p in result.stdout.decode("utf-8").split("\0") if p]


def test_no_internal_labels_in_tracked_files():
    tracked = _git_tracked_files()
    if tracked is None:
        pytest.skip("not inside a git checkout -- nothing to scan")

    offenses = []
    for rel_path in tracked:
        path = _REPO_ROOT / rel_path
        if path.resolve() == _THIS_FILE:
            continue  # this file's own docstrings describe the label shapes
        if not path.is_file():
            continue  # a tracked submodule/symlink target that isn't here
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue  # binary or unreadable -- not a text file we can scan

        posix_path = Path(rel_path).as_posix()
        for name, line_no, line in _scan_text(text):
            if _is_allowed(posix_path, line):
                continue
            offenses.append(f"{posix_path}:{line_no}: [{name}] {line.strip()}")

    assert not offenses, "internal-artefact labels found:\n" + "\n".join(offenses)


def test_no_decision_ids_in_changelog():
    """Decision ids are a legitimate dotagents citation everywhere else, but
    CHANGELOG.md is released history: it should say what shipped, not point a
    reader at a private design-log entry (`.agents/decisions/D<nn>.md`) they
    cannot open. Scoped to this one file by explicit repo policy."""
    tracked = _git_tracked_files()
    if tracked is None:
        pytest.skip("not inside a git checkout -- nothing to scan")
    if _CHANGELOG_PATH not in tracked:
        pytest.skip("no %s in this checkout" % _CHANGELOG_PATH)

    text = (_REPO_ROOT / _CHANGELOG_PATH).read_text(encoding="utf-8")
    offenses = [
        f"{_CHANGELOG_PATH}:{line_no}: {line.strip()}"
        for line_no, line in enumerate(text.splitlines(), start=1)
        if _DECISION_ID_RE.search(line)
    ]
    assert not offenses, "decision ids found in released history:\n" + "\n".join(
        offenses
    )


def test_label_patterns_catch_a_planted_offender():
    """Each pattern above actually matches a realistic offending line.

    Every sample is built from separate pieces and joined at the assertion,
    so the literal offending text never appears in this file's own source.
    """
    samples = {
        "numbered-plan": " ".join(["See", "Pl" + "an", "7", "for context."]),
        "numbered-phase": " ".join(["Start", "Ph" + "ase", "2", "now."]),
        "numbered-item": " ".join(["Fixes", "it" + "em", "3", "from the list."]),
        "task-range": "Measured after " + "T1" + "-" + "T7" + " landed.",
        "work-item-code-paren": "Async support " + "(" + "F4" + ")" + " added.",
        "work-item-code-bold": "- **" + "F4" + "** Async support added.",
        "work-item-code-bare": "See " + "F4" + "/" + "P2" + " for the change.",
        "reviewer-reference": "Mirrors the " + "review" + "er's fixture.",
        "in-depth-review": "Findings from the " + "in-depth" + " review.",
        "review-finding": "A " + "review find" + "ing about MCP.",
        "dated-review-reference": "Fixed per " + "review" + " " + "2026-09-23" + ".",
        "backlog-item": "Three fixes from the " + "backl" + "og.",
    }
    assert set(samples) == {
        name for name, _ in _PATTERNS
    }, "every pattern above must have a planted-offender sample"
    for name, pattern in _PATTERNS:
        matches = list(_scan_text(samples[name]))
        assert (
            matches and matches[0][0] == name
        ), f"pattern {name!r} failed to catch its own planted offender"


def test_decision_id_pattern_catches_a_planted_offender():
    """Exercised separately from `_PATTERNS` since it only applies to one
    file; same construction-from-pieces discipline as the test above."""
    sample = "Per " + "D" + "01" + ", the SDK stays optional."
    assert _DECISION_ID_RE.search(sample)
    sample_wikilink = "See " + "[[D" + "22" + "]]" + " for the rationale."
    assert _DECISION_ID_RE.search(sample_wikilink)
