"""`dotagents leak-check` -- scan a working tree for private material that must not ship.

Usage:
    dotagents leak-check [repo] [--plans DIR] [--patterns FILE] [--commits-only]

A command module shipped by this overlay: installing the overlay is what makes
the command exist. A same-named command in the store's own `dotagents/cmds/`
overrides it.

What it looks for
-----------------
* a path into the private notes directory, either slash;
* ``Phase N`` phrasing: a plan's own structure narrated into shipped text;
* a decision-log id, bare (``D<nn>``) or as a wiki link;
* a plan label: ``Plan`` or ``Decision`` followed by a number;
* the basename of every plan under the nearest private ``plans/`` directory at
  or above the repository, subdirectories included. A distinctive basename is
  a hit; a single generic word is a warning, since a public document may
  legitimately carry that name;
* personal markers (user names, home-directory paths, private repository
  names) and internal-infrastructure rules (host names, private address
  ranges, test accounts, credentials), both read from a machine-local pattern
  file. Nothing personal is built in;
* agent attribution in commit messages: a ``Co-Authored-By:`` trailer, any
  ``*-Session:`` trailer, a "Generated with" footer carrying a link or naming
  an assistant, and a session link.

What is not a hit
-----------------
* The bare filename ``AGENTS.md``: a committed, public file that tracked text
  legitimately names.
* The repository's own name. Its git top-level directory's basename and the
  package names ``pyproject.toml``, ``Cargo.toml`` and ``package.json``
  declare (``-``/``_`` spelt both ways) are blanked, as whole tokens, before
  the personal markers are matched. The header counts them and never prints
  them, since a name may equal a marker.
* A packaging manifest excluding the private directory: in ``pyproject.toml``,
  ``Cargo.toml`` or ``package.json`` a quoted string that is only a ``.agents``
  path pattern (``"/.agents"``, ``".agents/**"``, ...), and in ``MANIFEST.in``
  a ``prune``/``exclude``/``global-exclude``/``recursive-exclude`` line.

The agent store is refused
--------------------------
The store (``$AGENTS_HOME``, else ``~/.agents``), anything inside it, and any
checkout of the store's own git repository is private by design and ships
nowhere, so scanning it is exit 3 with the reason.

Per-repository exemptions
-------------------------
``<repo>/.agents/leak-check.toml`` (private, so never shipped) may name hit
classes that do not count toward the verdict, in the tree or in commit
messages::

    exempt = ["decision id", "plan label"]

The classes are ``.agents path``, ``'Phase N'``, ``decision id``,
``plan basename`` and ``plan label``; any other name is exit 2. Exempted hits
are still counted per file (``EXEMPT <file>: N <class>``).

The pattern file
----------------
``$DOTAGENTS_AUDIT_PATTERNS``, else ``audit_patterns.local.json`` in the agent
store (``$AGENTS_HOME``, else ``~/.agents``); ``--patterns`` overrides both.
Every key is optional::

    {"personal": ["literal", "..."],
     "public_allowlist": ["https://github.com/<org>"],
     "infra": [{"id": "lab-host",
                "why": "one line: why this must not reach a consumer",
                "regex": "(?<![0-9A-Za-z_])(?:buildhost|labsrv)(?![0-9A-Za-z_])",
                "ignorecase": true}]}

``personal`` entries are literal substrings. Each ``public_allowlist`` entry is
blanked from a line before matching, so a published organisation name inside a
URL does not trip a user-name pattern while a real home-directory path still
does. An ``infra`` rule needs ``id`` and ``regex``; give it explicit
boundaries, because a rule that fires on ordinary words gets ignored.

A class of pattern that did not run is reported as INCOMPLETE, never as PASS:
no pattern file, an empty list, a rule whose regex did not compile, or no
plans directory each say so in a banner and in the verdict line.

A tree that ships nowhere
-------------------------
Workspace machinery with no consumer may carry a ``.leak-check-ships-nowhere``
file at its root::

    ships: nowhere
    reason: <one line saying why nothing here reaches a consumer>

Both keys are required. The marker is a claim, and the tool checks it: it is
refused when the tree has a git remote or a packaging manifest at its root. A
refused marker changes nothing. An honoured one still prints every hit, tagged
``[ships-nowhere]``, and only the verdict becomes PASS.

Exit codes
----------
    0  PASS           nothing found
    1  FAIL           hits to judge
    2  usage error, or an unusable exemptions file
    3  CANNOT CHECK   the tree could not be enumerated, held no readable
                      file, or is the agent store

Judge by the exit code. Do not pipe the output through ``tail``: the pipe
returns tail's status and the verdict is lost.

Python 3.9+; standard library and `duho`.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from collections import OrderedDict
from typing import Annotated

from duho import Cmd, LoggingArgs, Meta

try:
    import tomllib as _toml
except ImportError:  # Python < 3.11
    try:
        import tomli as _toml  # type: ignore[no-redef]
    except ImportError:
        _toml = None  # type: ignore[assignment]

EXIT_OK = 0
EXIT_HITS = 1
EXIT_USAGE = 2
EXIT_CANNOT_CHECK = 3

OPTOUT_NAME = ".leak-check-ships-nowhere"

# Files that legitimately name what this tool hunts for.
SKIP_NAMES = {".gitignore", OPTOUT_NAME}

# Evidence, at the tree root, that something packages it. Cargo is handled
# separately: a `[workspace]`-only manifest publishes nothing itself.
SHIPPING_MANIFESTS = (
    "pyproject.toml", "setup.py", "setup.cfg", "package.json", "pubspec.yaml",
    "Gemfile", "composer.json", "go.mod",
)
SHIPPING_MANIFEST_GLOBS = ("*.gemspec", "*.podspec", "*.nuspec")

# Used only by the filesystem walk of last resort, when git cannot be run. It
# is not a substitute for .gitignore, and the mode line says so.
PRUNE_DIRS = {
    ".git", ".agents", ".claude", ".idea", ".vscode", ".venv", ".dart_tool",
    "__pycache__", "node_modules", "target", "_target", "build", "_build",
    "dist", ".pytest_cache", ".mypy_cache", ".ruff_cache",
}

# Preceded by a word character, `.` or `)` it is an attribute access
# (`cfg.agents`, `Set().agents`) or a relative import (`from ..agents`), and
# right after `from ` a relative import of a module named `agents`: not a path.
AGENTS_RE = re.compile(r"(?<![\w.)])(?<!from )\.agents\b")
PHASE_RE = re.compile(r"\bPhase [0-9]")
# The bare form needs the boundary and exactly two digits, or it fires on
# ordinary identifiers: a register name, a hex literal, `AMD64`.
DECISION_RE = re.compile(r"\[\[D[0-9]{2}\]\]|(?<![0-9A-Za-z_])D[0-9]{2}\b")
# Capitalised and followed by a number: a private plan or decision label. Short
# fix codes are not matched, since they read the same as register names.
PLAN_LABEL_RE = re.compile(r"\b(?:Plan|Decision) [0-9]+\b")

# Packaging manifests whose exclusion lists name `.agents` to keep it out.
MANIFEST_NAMES = {"pyproject.toml", "Cargo.toml", "package.json"}
# A quoted string that is nothing but a `.agents` path pattern (`"/.agents"`,
# `'.agents/**'`, `"!.agents"`, ...); any other text in the string makes it a
# reference.
AGENTS_EXCLUSION_RE = re.compile(r"""(["'])!?(?:\*\*/|\./|/)?\.agents(?:/[*/]*)?\1""")
MANIFEST_IN_EXCLUDE_RE = re.compile(
    r"^\s*(?:prune|exclude|global-exclude|recursive-exclude)(?:\s|$)")

# Per-repository exemptions, read from the repository's private `.agents/`.
EXEMPTIONS_NAME = "leak-check.toml"
# The hit classes a repository may exempt. Personal markers, infra rules and
# attribution trailers are never exemptable.
EXEMPTABLE = (".agents path", "'Phase N'", "decision id", "plan basename",
              "plan label")
HIT_RE = re.compile(r"^(?P<rel>.+?):(?P<lineno>[0-9]+): (?P<what>.*)$")
# Trailers are anchored to line start: a trailer is a `Key: value` line, and a
# commit that discusses the rule mentions it inline. "Generated with" is
# ordinary English, so it is a hit only with the emoji, a link on the line, or
# a named assistant. The session link is a hit wherever it appears.
TRAILER_RE = re.compile(
    r"^[ \t]*Co-Authored-By[ \t]*:"
    r"|^[ \t]*[A-Za-z][A-Za-z-]*-Session[ \t]*:"
    r"|^[ \t]*\U0001F916[ \t]*Generated with\b"
    r"|^[ \t]*Generated with[ \t][^\n]*(?:\[[^\]]+\]\(|https?://)"
    r"|^[ \t]*Generated with[ \t]+\[?"
    r"(?:Claude|Copilot|Cursor|Codex|ChatGPT|Gemini|Devin|an? AI\b|AI\b)"
    r"|claude\.ai/code/session[_/][A-Za-z0-9]",
    re.MULTILINE | re.IGNORECASE,
)

# A basename with an underscore, a hyphen or a digit is distinctive enough that
# a shipped file naming it is citing the plan. A single lowercase word is not.
DISTINCTIVE_RE = re.compile(r"[_\-0-9]")

PATTERNS_ENV = "DOTAGENTS_AUDIT_PATTERNS"
DEFAULT_PATTERNS = (Path(os.environ.get("AGENTS_HOME") or Path.home() / ".agents")
                    / "audit_patterns.local.json")


class CannotCheck(Exception):
    """The tree could not be examined. Never reported as PASS or as FAIL."""


class UsageError(Exception):
    """A configuration the tool refuses to guess about: exit 2."""


def personal_patterns_path(override: str | None) -> Path:
    """The pattern file: the option or the variable (duho folds them into
    `override`), else the agent store's."""
    return Path(override) if override else DEFAULT_PATTERNS


def load_personal_patterns(path: Path) -> tuple[list[str], list[str], str | None]:
    """(markers, allowlist, why-it-is-empty).

    A missing or unreadable file is not an error: it yields no markers and a
    reason, which `main` reports as INCOMPLETE.
    """
    if not path.is_file():
        return [], [], "no pattern file at %s" % path
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        return [], [], "could not read %s (%s)" % (path, exc)
    if not isinstance(data, dict):
        return [], [], "%s is not a JSON object" % path
    markers = [str(p) for p in data.get("personal", []) if str(p)]
    allowlist = [str(p) for p in data.get("public_allowlist", []) if str(p)]
    if not markers:
        return [], allowlist, "%s declares no `personal` patterns" % path
    return markers, allowlist, None


def load_infra_rules(path: Path) -> tuple[list[tuple[str, re.Pattern]], str | None,
                                          list[str]]:
    """(rules, why-it-is-empty, broken) for the internal-infrastructure rules.

    `broken` names every rule that could not be used. A rule is never dropped
    silently and never aborts the run.
    """
    if not path.is_file():
        return [], "no pattern file at %s" % path, []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        return [], "could not read %s (%s)" % (path, exc), []
    if not isinstance(data, dict):
        return [], "%s is not a JSON object" % path, []

    raw = data.get("infra", [])
    if not isinstance(raw, list) or not raw:
        return [], "%s declares no `infra` rules" % path, []

    rules: list[tuple[str, re.Pattern]] = []
    broken: list[str] = []
    for index, entry in enumerate(raw):
        if not isinstance(entry, dict):
            broken.append("infra[%d] is not an object" % index)
            continue
        rule_id = str(entry.get("id") or "infra[%d]" % index)
        pattern = entry.get("regex")
        if not pattern:
            broken.append("%s has no `regex`" % rule_id)
            continue
        flags = re.IGNORECASE if entry.get("ignorecase") else 0
        try:
            rules.append((rule_id, re.compile(str(pattern), flags)))
        except re.error as exc:
            broken.append("%s: bad regex (%s)" % (rule_id, exc))
    if not rules:
        return [], ("%s declares %d `infra` rule(s), NONE of which compiled"
                    % (path, len(raw))), broken
    return rules, None, broken


def shipping_evidence(repo: Path) -> list[str]:
    """Every sign the tool can see that this tree reaches somebody."""
    evidence: list[str] = []

    out = _git(["-C", str(repo), "remote"])
    if out.returncode == 0:
        remotes = out.stdout.decode("utf-8", "replace").split()
        if remotes:
            evidence.append("git remote(s) configured: %s" % ", ".join(remotes))

    cargo = repo / "Cargo.toml"
    if cargo.is_file():
        try:
            text = cargo.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        if re.search(r"(?m)^\s*\[package\]", text):
            evidence.append("Cargo.toml declares [package]")

    for name in SHIPPING_MANIFESTS:
        if (repo / name).is_file():
            evidence.append("packaging manifest: %s" % name)
    for pattern in SHIPPING_MANIFEST_GLOBS:
        for hit in sorted(repo.glob(pattern)):
            evidence.append("packaging manifest: %s" % hit.name)

    return evidence


def read_optout(repo: Path) -> tuple[str, str, list[str]]:
    """(state, reason, problems); state is "absent", "honoured" or "refused"."""
    marker = repo / OPTOUT_NAME
    if not marker.is_file():
        return "absent", "", []
    try:
        text = marker.read_text(encoding="utf-8")
    except OSError as exc:
        return "refused", "", ["%s could not be read (%s)" % (OPTOUT_NAME, exc)]

    fields: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition(":")
        if sep:
            fields[key.strip().lower()] = value.strip()

    problems: list[str] = []
    if fields.get("ships", "").lower() != "nowhere":
        problems.append("no `ships: nowhere` line")
    reason = fields.get("reason", "")
    if not reason:
        problems.append("no non-empty `reason:` line -- an exemption with no "
                        "recorded reason is the silent exemption this marker "
                        "exists to prevent")
    problems.extend(shipping_evidence(repo))
    return ("refused" if problems else "honoured"), reason, problems


def find_plans_dir(start: Path) -> Path | None:
    """The nearest private `plans/` directory at or above `start`.

    Upward, because in a workspace of several repositories the plans sit at the
    workspace root, outside every one of them.
    """
    here = start.resolve()
    for candidate in (here, *here.parents):
        plans = candidate / ".agents" / "plans"
        if plans.is_dir():
            return plans
    return None


def harvest_plan_names(plans_dir: Path) -> tuple[set[str], set[str]]:
    """(distinctive, generic) plan basenames, from every subdirectory."""
    distinctive: set[str] = set()
    generic: set[str] = set()
    for path in plans_dir.rglob("*.md"):
        if path.stem.lower() in ("readme", "index"):
            continue
        target = distinctive if DISTINCTIVE_RE.search(path.stem) else generic
        target.add(path.name)
    return distinctive, generic


def alternation(names: set[str]) -> re.Pattern | None:
    """One regex for a basename set, longest first, so the report names the
    longest plan that matched."""
    if not names:
        return None
    ordered = sorted(names, key=lambda n: (-len(n), n))
    return re.compile("|".join(re.escape(n) for n in ordered))


def _git(args: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], capture_output=True,
                          stdin=subprocess.DEVNULL, **kw)


def is_git_worktree(repo: Path) -> bool:
    try:
        out = _git(["-C", str(repo), "rev-parse", "--is-inside-work-tree"])
    except OSError:
        return False
    return out.returncode == 0 and out.stdout.strip() == b"true"


def git_toplevel(path: Path) -> Path | None:
    try:
        out = _git(["-C", str(path), "rev-parse", "--show-toplevel"])
    except OSError:
        return None
    text = out.stdout.decode("utf-8", "surrogateescape").strip()
    return Path(text).resolve() if out.returncode == 0 and text else None


def git_common_dir(path: Path) -> Path | None:
    """The repository every worktree of `path` shares (relative output is
    relative to `path`, which is how older git prints it)."""
    try:
        out = _git(["-C", str(path), "rev-parse", "--git-common-dir"])
    except OSError:
        return None
    text = out.stdout.decode("utf-8", "surrogateescape").strip()
    if out.returncode != 0 or not text:
        return None
    common = Path(text)
    return (common if common.is_absolute() else path / common).resolve()


def agents_store() -> Path:
    """The user's agent store: `$AGENTS_HOME`, else `~/.agents`."""
    return Path(os.environ.get("AGENTS_HOME") or Path.home() / ".agents")


def store_refusal(repo: Path) -> str | None:
    """Why `repo` (resolved) is the private agent store, or None.

    The store's git repository counts only when the store is its top level,
    so a store inside some other checkout does not claim that checkout."""
    try:
        store = agents_store().resolve()
    except OSError:
        return None
    if repo == store or store in repo.parents:
        return "%s is the agent store (%s) or lies inside it" % (repo, store)
    if not store.is_dir() or not repo.is_dir() or git_toplevel(store) != store:
        return None
    store_common = git_common_dir(store)
    if store_common is not None and git_common_dir(repo) == store_common:
        return ("%s is a checkout of the agent store's repository (%s)"
                % (repo, store_common))
    return None


def _declared_name(path: Path) -> str | None:
    """The package name a manifest declares, or None if it cannot be read."""
    try:
        text = path.read_text(encoding="utf-8")
        if path.name == "package.json":
            data = json.loads(text)
            name = data.get("name") if isinstance(data, dict) else None
        else:
            if _toml is None:
                return None
            table = _toml.loads(text).get(
                "project" if path.name == "pyproject.toml" else "package")
            name = table.get("name") if isinstance(table, dict) else None
    except (ValueError, OSError):
        return None
    return name if isinstance(name, str) and name.strip() else None


def own_names(repo: Path) -> list[str]:
    """The names a repository publishes itself under: its top-level
    directory's basename and the package names its manifests declare, each
    with `-`/`_` spelt both ways, longest first."""
    top = git_toplevel(repo)
    roots = [repo] if top is None or top == repo else [repo, top]
    names = {(top or repo).name}
    for root in roots:
        for manifest in sorted(MANIFEST_NAMES):
            path = root / manifest
            if path.is_file():
                names.add(_declared_name(path) or "")
    forms: set[str] = set()
    for name in names:
        name = name.strip()
        forms.update(f for f in (name, name.replace("-", "_"), name.replace("_", "-")) if f)
    return sorted(forms, key=lambda f: (-len(f), f))


def own_names_re(names: list[str]) -> re.Pattern | None:
    """Each name as a whole token, so a short repository name cannot blank
    part of a longer marker."""
    if not names:
        return None
    return re.compile("|".join(r"(?<![0-9A-Za-z])%s(?![0-9A-Za-z])" % re.escape(n)
                               for n in names))


def load_exemptions(repo: Path) -> tuple[set[str], Path | None]:
    """(exempt hit classes, the file they came from) from the repository's
    private `.agents/leak-check.toml`, at the target or its git top level."""
    candidates = [repo / ".agents" / EXEMPTIONS_NAME]
    top = git_toplevel(repo)
    if top is not None and top != repo:
        candidates.append(top / ".agents" / EXEMPTIONS_NAME)
    path = next((c for c in candidates if c.is_file()), None)
    if path is None:
        return set(), None
    if _toml is None:
        raise UsageError("%s cannot be read: no TOML parser (Python below 3.11 "
                         "needs tomli)" % path)
    try:
        data = _toml.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        raise UsageError("%s could not be read (%s)" % (path, exc)) from None
    raw = data.get("exempt", [])
    if not isinstance(raw, list) or not all(isinstance(x, str) for x in raw):
        raise UsageError("%s: `exempt` must be a list of hit class names" % path)
    unknown = [x for x in raw if x not in EXEMPTABLE]
    if unknown:
        raise UsageError("%s: unknown hit class(es) %s; the known ones are %s"
                         % (path, ", ".join(repr(x) for x in unknown),
                            ", ".join(repr(k) for k in EXEMPTABLE)))
    return set(raw), path


def hit_class(hit: str) -> str | None:
    """The exemptable class of a tree hit (`"<rel>:<lineno>: <what>"`)."""
    match = HIT_RE.match(hit)
    if not match:
        return None
    what = match.group("what")
    for cls in EXEMPTABLE:
        if what == cls or what.startswith(cls + " "):
            return cls
    return None


def split_exempt(hits: list[str], commit_hits: list[tuple[str, str, str]],
                 exempt: set[str]):
    """(kept tree hits, kept commit hits, {where: {class: count}} exempted)."""
    counts: OrderedDict[str, OrderedDict[str, int]] = OrderedDict()
    kept: list[str] = []
    for hit in hits:
        cls = hit_class(hit)
        if cls in exempt:
            where = HIT_RE.match(hit).group("rel")
            per = counts.setdefault(where, OrderedDict())
            per[cls] = per.get(cls, 0) + 1
        else:
            kept.append(hit)
    kept_commits: list[tuple[str, str, str]] = []
    for sha, kind, text in commit_hits:
        if kind in exempt:
            per = counts.setdefault("commit %s" % sha, OrderedDict())
            per[kind] = per.get(kind, 0) + 1
        else:
            kept_commits.append((sha, kind, text))
    return kept, kept_commits, counts


def agents_reference(name: str, line: str) -> bool:
    """Whether `line` of a file called `name` names the private directory,
    leaving out a packaging manifest's own exclusion of it."""
    if name == "MANIFEST.in" and MANIFEST_IN_EXCLUDE_RE.match(line):
        return False
    if name in MANIFEST_NAMES:
        line = AGENTS_EXCLUSION_RE.sub("", line)
    return AGENTS_RE.search(line) is not None


def _split0(blob: bytes) -> list[str]:
    return [f for f in blob.decode("utf-8", "surrogateescape").split("\0") if f]


def walk_filesystem(repo: Path) -> list[str]:
    found = []
    stack = [repo]
    while stack:
        current = stack.pop()
        try:
            entries = list(current.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.is_symlink():
                continue
            if entry.is_dir():
                if entry.name not in PRUNE_DIRS:
                    stack.append(entry)
            elif entry.is_file():
                found.append(entry.relative_to(repo).as_posix())
    return found


def enumerate_files(repo: Path) -> tuple[list[str], str]:
    """Every file a reviewer would consider, and the mode that found them.

    Tracked and untracked, ignore rules honoured: a file just written is
    untracked, and that is where a new leak is likeliest.
    """
    if not repo.is_dir():
        raise CannotCheck("no such directory: %s" % repo)

    if is_git_worktree(repo):
        out = _git(["-C", str(repo), "ls-files", "--cached", "--others",
                    "--exclude-standard", "-z"])
        if out.returncode != 0:
            raise CannotCheck("git ls-files failed: %s"
                              % out.stderr.decode("utf-8", "replace").strip())
        return sorted(_split0(out.stdout)), (
            "git working tree (tracked AND untracked, .gitignore honoured)")

    # Not a repository: a scratch bare index still applies the tree's own
    # .gitignore files.
    scratch = None
    try:
        scratch = tempfile.mkdtemp(prefix="leakchk-")
        gitdir = str(Path(scratch) / "scratch.git")
        init = _git(["init", "--bare", "-q", gitdir])
        if init.returncode == 0:
            out = _git(["--git-dir", gitdir, "--work-tree", str(repo),
                        "ls-files", "--others", "--exclude-standard", "-z"])
            if out.returncode == 0:
                return sorted(_split0(out.stdout)), (
                    "working tree via a scratch git index "
                    "(NOT a git repository; .gitignore honoured)")
    except OSError:
        pass
    finally:
        if scratch:
            shutil.rmtree(scratch, ignore_errors=True)

    return sorted(walk_filesystem(repo)), (
        "filesystem walk (NOT a git repository, and git could not be used: "
        "NO ignore rules applied, only a built-in prune list)")


def unpushed_commits(repo: Path) -> set[str]:
    """Commits reachable from HEAD and from no remote-tracking branch: what the
    next push would publish (every commit, when there is no remote)."""
    out = _git(["-C", str(repo), "rev-list", "HEAD", "--not", "--remotes"])
    if out.returncode != 0:
        return set()
    return set(out.stdout.decode("ascii", "replace").split())


def scan_commits(repo: Path, plan_re=None, full: bool = False
                 ) -> tuple[list[tuple[str, str, str]], int, int]:
    """Commit messages: every one for attribution trailers, and the unpushed
    ones (every one with ``full``) for the tree's private-note patterns too --
    a pushed message can only be fixed by rewriting history.

    (hits as (sha, kind, matched text), commits examined, commits checked for
    the private-note patterns)."""
    out = _git(["-C", str(repo), "log", "--format=%x01%H%x02%B"])
    if out.returncode != 0:  # no commits yet
        return [], 0, 0
    unpushed = None if full else unpushed_commits(repo)
    hits: list[tuple[str, str, str]] = []
    examined = checked = 0
    for record in out.stdout.decode("utf-8", "replace").split("\x01"):
        if not record.strip():
            continue
        examined += 1
        sha, _, body = record.partition("\x02")
        sha = sha.strip()
        match = TRAILER_RE.search(body)
        if match:
            hits.append((sha[:12], "session trailer", match.group(0)))
        if unpushed is not None and sha not in unpushed:
            continue
        checked += 1
        for kind, pattern in ((".agents path", AGENTS_RE), ("'Phase N'", PHASE_RE),
                              ("decision id", DECISION_RE), ("plan label", PLAN_LABEL_RE),
                              ("plan basename", plan_re)):
            match = pattern.search(body) if pattern is not None else None
            if match:
                hits.append((sha[:12], kind, match.group(0)))
    return hits, examined, checked


def allowlist_forms(allowlist: list[str]) -> list[str]:
    """Each allowlisted string plus the spellings it takes in the wild, longest
    first: shields.io badge text escapes `-` as `--`, and a JNI symbol writes
    a dotted package with `_` (escaping its own `_` as `_1`)."""
    forms: list[str] = []
    for allowed in allowlist:
        for form in (allowed, allowed.replace("-", "--"),
                     allowed.replace("_", "_1").replace(".", "_")):
            if form and form not in forms:
                forms.append(form)
    return sorted(forms, key=len, reverse=True)


def scan_tree(repo: Path, files: list[str], plan_re, generic_re,
              personal: list[str] | None = None,
              allowlist: list[str] | None = None,
              infra: list[tuple[str, re.Pattern]] | None = None,
              own: list[str] | None = None):
    """(hits, warnings, files read, files skipped).

    `own` is the repository's own names (`own_names`), blanked before the
    personal markers are matched: a repository naming itself publishes only
    its own name."""
    hits: list[str] = []
    warnings: list[str] = []
    personal = personal or []
    allowlist = allowlist_forms(allowlist or [])
    infra = infra or []
    own_re = own_names_re(own or [])
    read = 0
    skipped = 0
    for rel in files:
        path = repo / rel
        if path.name in SKIP_NAMES:
            continue
        if not path.is_file() or path.is_symlink():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            skipped += 1  # binary or unreadable
            continue
        read += 1
        for lineno, line in enumerate(text.splitlines(), 1):
            where = "%s:%d" % (rel, lineno)
            probe = line
            if personal or infra:
                # One allowlist for both classes, blanked before matching.
                for allowed in allowlist:
                    probe = probe.replace(allowed, "")
            if personal:
                personal_probe = own_re.sub("", probe) if own_re else probe
                for marker in personal:
                    if marker in personal_probe:
                        hits.append("%s: personal marker %r" % (where, marker))
            for rule_id, rule_re in infra:
                match = rule_re.search(probe)
                if match:
                    hits.append("%s: internal infra [%s] %r"
                                % (where, rule_id, match.group(0)))
            if agents_reference(path.name, line):
                hits.append("%s: .agents path" % where)
            if PHASE_RE.search(line):
                hits.append("%s: 'Phase N'" % where)
            match = DECISION_RE.search(line)
            if match:
                hits.append("%s: decision id %r" % (where, match.group(0)))
            match = PLAN_LABEL_RE.search(line)
            if match:
                hits.append("%s: plan label %r" % (where, match.group(0)))
            if plan_re:
                match = plan_re.search(line)
                if match:
                    hits.append("%s: plan basename %r" % (where, match.group(0)))
            if generic_re:
                match = generic_re.search(line)
                if match:
                    warnings.append("%s: generic plan basename %r "
                                    "(judge: may be a real public filename)"
                                    % (where, match.group(0)))
    return hits, warnings, read, skipped


class LeakCheck(LoggingArgs, Cmd):
    """Scan a working tree and its commit messages for private material that must not ship."""

    _parsername_ = "leak-check"

    repo: Path = Path(".")
    "Repository or directory to scan (default: the current directory)."
    ("repo",)

    plans: str = ""
    "Plans directory to harvest basenames from (default: the nearest one at or above the repo)."
    ("--plans",)

    patterns: Annotated[str, Meta(env=PATTERNS_ENV)] = ""
    "Machine-local pattern file (default: $DOTAGENTS_AUDIT_PATTERNS, else the agent store's)."
    ("--patterns",)

    commits_only: bool = False
    "Scan only commit messages, skipping the tree."
    ("--commits-only",)

    full: bool = False
    ("Check every commit message for private-note patterns, not only the commits "
     "not yet pushed (trailers are checked in all of them either way).")
    ("--full", "--all")

    def __call__(self) -> int:
        repo = Path(self.repo).resolve()
        print("repo:     %s" % repo)
        refusal = store_refusal(repo)
        if refusal:
            print("mode:     CANNOT CHECK")
            print("\nCANNOT CHECK: %s." % refusal)
            print("The agent store is private by design and ships nowhere; "
                  "leak-check checks what ships. Run it on a repository that "
                  "publishes something.")
            return EXIT_CANNOT_CHECK
        try:
            self._exempt, self._exempt_path = load_exemptions(repo)
        except UsageError as exc:
            print("exempt:   UNUSABLE -- %s" % exc, file=sys.stderr)
            return EXIT_USAGE
        if self._exempt_path is not None:
            print("exempt:   %s (from %s)"
                  % (", ".join(c for c in EXEMPTABLE if c in self._exempt) or "nothing",
                     self._exempt_path))
        optout_state, optout_reason, optout_problems = self._optout(repo)

        if self.plans:
            plans_dir: Path | None = Path(self.plans).resolve()
            if not plans_dir.is_dir():
                print("plans:    NOT A DIRECTORY: %s" % plans_dir, file=sys.stderr)
                return EXIT_USAGE
        else:
            plans_dir = find_plans_dir(repo)
        distinctive: set[str] = set()
        generic: set[str] = set()
        if plans_dir:
            distinctive, generic = harvest_plan_names(plans_dir)
        print("plans:    %s" % (plans_dir if plans_dir else "NONE FOUND"))

        if self.commits_only:
            return self._commit_messages(repo, alternation(distinctive))

        patterns_file = personal_patterns_path(self.patterns or None)
        personal, allowlist, personal_gap = load_personal_patterns(patterns_file)
        print("personal: %s"
              % (personal_gap if personal_gap
                 else "%d marker(s) from %s (%d allowlisted)"
                      % (len(personal), patterns_file, len(allowlist))))
        infra, infra_gap, infra_broken = load_infra_rules(patterns_file)
        print("infra:    %s"
              % (infra_gap if infra_gap
                 else "%d rule(s) from %s: %s"
                      % (len(infra), patterns_file,
                         ", ".join(rule_id for rule_id, _ in infra))))
        for problem in infra_broken:
            print("infra:    RULE DID NOT COMPILE -- %s" % problem)
        # Counted, never listed: a repository's name may equal a private marker.
        own = own_names(repo)
        print("own name: %d name(s) allowlisted for personal markers "
              "(the repository's own, not printed)" % len(own))

        try:
            files, mode = enumerate_files(repo)
        except CannotCheck as exc:
            print("mode:     CANNOT CHECK")
            print("\nCANNOT CHECK: %s" % exc)
            print("This is exit %d, not %d and not %d: nothing was examined, and "
                  "that must not be readable as either answer."
                  % (EXIT_CANNOT_CHECK, EXIT_OK, EXIT_HITS))
            return EXIT_CANNOT_CHECK
        print("mode:     %s" % mode)

        hits, warnings, read, skipped = scan_tree(
            repo, files, alternation(distinctive), alternation(generic),
            personal, allowlist, infra, own=own)
        commit_hits: list[tuple[str, str, str]] = []
        commits = checked = 0
        if is_git_worktree(repo):
            commit_hits, commits, checked = scan_commits(
                repo, alternation(distinctive), self.full)
        hits, commit_hits, exempted = split_exempt(hits, commit_hits, self._exempt)

        print("files:    %d listed, %d read, %d skipped (binary or unreadable)"
              % (len(files), read, skipped))
        print("patterns: .agents path + 'Phase N' + decision ids + plan labels, "
              "%d plan basenames (%d strict, %d generic->warn), "
              "%d personal marker(s), %d infra rule(s)"
              % (len(distinctive) + len(generic), len(distinctive), len(generic),
                 len(personal), len(infra)))
        print(self._commits_line(commits, checked))
        print()

        tag = "[ships-nowhere] " if optout_state == "honoured" else ""
        for line in hits:
            print("%s%s" % (tag, line))
        for sha, kind, text in commit_hits:
            print("%scommit %s: %s %r" % (tag, sha, kind, text))
        self._print_exempted(exempted)
        for line in warnings:
            print("WARN %s%s" % (tag, line))
        if optout_state == "refused":
            self._print_refusal(optout_problems)

        # A class that did not run must not read as a class that found nothing:
        # say it in a banner, and again in the verdict line.
        empty: list[str] = []
        if not distinctive and not generic:
            empty.append("plan basenames")
        if not personal:
            empty.append("personal markers")
        if not infra:
            empty.append("internal-infrastructure rules")
        elif infra_broken:
            empty.append("%d infra rule(s) that did not compile" % len(infra_broken))
        if read == 0:
            empty.append("files")
        if empty:
            self._print_incomplete(empty, plans_dir, patterns_file, personal_gap,
                                   infra_gap, infra_broken)

        if read == 0:
            print("\nCANNOT CHECK: 0 readable files under %s" % repo)
            return EXIT_CANNOT_CHECK

        total = len(hits) + len(commit_hits)
        suffix = ""
        if warnings:
            suffix += " [%d warning%s]" % (len(warnings), "" if len(warnings) == 1 else "s")
        suffix += self._exempt_suffix(exempted)
        if empty:
            suffix += " [INCOMPLETE: %s not checked]" % ", ".join(empty)
        if total and optout_state == "honoured":
            print("\nPASS%s [%d hit%s NOT counted: %s -- %s]"
                  % (suffix, total, "" if total == 1 else "s", OPTOUT_NAME,
                     optout_reason))
            return EXIT_OK
        if total:
            print("\nFAIL (%d hits)%s" % (total, suffix))
            return EXIT_HITS
        print("\nPASS%s" % suffix)
        return EXIT_OK

    def _optout(self, repo: Path) -> tuple[str, str, list[str]]:
        """Read the ships-nowhere marker and report what was decided about it."""
        state, reason, problems = read_optout(repo)
        if state == "honoured":
            print("optout:   %s -- %s" % (OPTOUT_NAME, reason))
        elif state == "refused":
            print("optout:   %s REFUSED (see below)" % OPTOUT_NAME)
        else:
            print("optout:   none")
        return state, reason, problems

    def _commits_line(self, commits: int, checked: int) -> str:
        return ("commits:  %d examined for session trailers, %d %s for private-note "
                "patterns%s" % (commits, checked, "(all)" if self.full else "unpushed",
                               "" if self.full else " (--full checks every commit)"))

    def _commit_messages(self, repo: Path, plan_re=None) -> int:
        """`--commits-only`: the commit-message scan alone.

        For a repository that tracks text about the private notes on purpose,
        where the tree scan would drown the commit-message signal.
        """
        if not is_git_worktree(repo):
            print("mode:     CANNOT CHECK")
            print("\nCANNOT CHECK: not a git repository, so there are no commit "
                  "messages to scan: %s" % repo)
            return EXIT_CANNOT_CHECK
        hits, commits, checked = scan_commits(repo, plan_re, self.full)
        _, hits, exempted = split_exempt([], hits, self._exempt)
        print("mode:     commit messages only (tree scan SKIPPED)")
        print(self._commits_line(commits, checked))
        print()
        if commits == 0:
            print("CANNOT CHECK: 0 commits under %s" % repo)
            return EXIT_CANNOT_CHECK
        for sha, kind, text in hits:
            print("commit %s: %s %r" % (sha, kind, text))
        self._print_exempted(exempted)
        suffix = self._exempt_suffix(exempted)
        if hits:
            print("\nFAIL (%d hits)%s [commit messages only; the tree was NOT "
                  "scanned]" % (len(hits), suffix))
            return EXIT_HITS
        print("\nPASS%s [commit messages only; the tree was NOT scanned]" % suffix)
        return EXIT_OK

    @staticmethod
    def _print_exempted(exempted) -> None:
        """One line per file (or commit): what the exemptions kept out of the
        verdict, counted by class."""
        for where, per in exempted.items():
            print("EXEMPT %s: %s" % (where, ", ".join(
                "%d %s" % (n, cls) for cls, n in per.items())))

    def _exempt_suffix(self, exempted) -> str:
        total = sum(sum(per.values()) for per in exempted.values())
        if not total:
            return ""
        return " [%d hit%s exempt by %s]" % (total, "" if total == 1 else "s",
                                             self._exempt_path)

    @staticmethod
    def _print_refusal(problems: list[str]) -> None:
        print()
        print("!" * 72)
        print("!! %s IS PRESENT AND WAS REFUSED. This run is exactly what it"
              % OPTOUT_NAME)
        print("!! would have been with no marker at all: the hits above stand "
              "and the")
        print("!! verdict below is unchanged. Reason(s):")
        for problem in problems:
            print("!!   - %s" % problem)
        print("!" * 72)

    @staticmethod
    def _print_incomplete(empty: list[str], plans_dir: Path | None,
                          patterns_file: Path, personal_gap: str | None,
                          infra_gap: str | None, infra_broken: list[str]) -> None:
        print()
        print("!" * 72)
        for what in empty:
            if what == "internal-infrastructure rules":
                print("!! NO INTERNAL-INFRASTRUCTURE RULES WERE LOADED -- %s."
                      % infra_gap)
                print("!! That class did NOT run: host names, private address "
                      "ranges, test")
                print("!! accounts and credentials were not looked for. Add an "
                      "`infra` list to")
                print("!! %s" % patterns_file)
                print("!! (or point --patterns / $%s at a file that has one)."
                      % PATTERNS_ENV)
            elif what.endswith("that did not compile"):
                print("!! AN INFRA RULE DID NOT COMPILE, so that rule did NOT "
                      "run. The verdict")
                print("!! below covers the rules that did and nothing about "
                      "these:")
                for problem in infra_broken:
                    print("!!   - %s" % problem)
            elif what == "personal markers":
                print("!! NO PERSONAL MARKERS WERE LOADED -- %s." % personal_gap)
                print("!! That class did NOT run: user names, home-directory "
                      "paths and private")
                print("!! repository names were not looked for. Point "
                      "--patterns (or $%s)" % PATTERNS_ENV)
                print("!! at a file, or write one at %s." % DEFAULT_PATTERNS)
            elif what == "plan basenames":
                print("!! NO PLAN BASENAMES WERE HARVESTED"
                      + (" from %s." % plans_dir if plans_dir
                         else " -- no plans directory exists at or above this "
                              "repo."))
                print("!! That half of the check did NOT run. The verdict "
                      "below says nothing about")
                print("!! plan filenames. Pass --plans <dir> if the plans live "
                      "somewhere else.")
            else:
                print("!! NOT ONE FILE WAS READ. There is nothing here this "
                      "tool can check.")
        print("!" * 72)
