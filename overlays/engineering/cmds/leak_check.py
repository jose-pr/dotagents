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

The bare filename ``AGENTS.md`` is not a pattern: it is a committed, public
file that tracked text legitimately names.

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
    2  usage error
    3  CANNOT CHECK   the tree could not be enumerated, or held no readable
                      file

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

from duho import Cmd, LoggingArgs

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

# Preceded by a word character it is an attribute access (`cfg.agents`), not a
# path.
AGENTS_RE = re.compile(r"(?<!\w)\.agents\b")
PHASE_RE = re.compile(r"\bPhase [0-9]")
# The bare form needs the boundary and exactly two digits, or it fires on
# ordinary identifiers: a register name, a hex literal, `AMD64`.
DECISION_RE = re.compile(r"\[\[D[0-9]{2}\]\]|(?<![0-9A-Za-z_])D[0-9]{2}\b")
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


def personal_patterns_path(override: str | None) -> Path:
    if override:
        return Path(override)
    raw = os.environ.get(PATTERNS_ENV)
    return Path(raw) if raw else DEFAULT_PATTERNS


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


def scan_commits(repo: Path) -> tuple[list[tuple[str, str]], int]:
    """Attribution trailers in commit messages. (hits, commits examined)."""
    out = _git(["-C", str(repo), "log", "--format=%x01%H%x02%B"])
    if out.returncode != 0:  # no commits yet
        return [], 0
    hits = []
    examined = 0
    for record in out.stdout.decode("utf-8", "replace").split("\x01"):
        if not record.strip():
            continue
        examined += 1
        sha, _, body = record.partition("\x02")
        match = TRAILER_RE.search(body)
        if match:
            hits.append((sha.strip()[:12], match.group(0)))
    return hits, examined


def scan_tree(repo: Path, files: list[str], plan_re, generic_re,
              personal: list[str] | None = None,
              allowlist: list[str] | None = None,
              infra: list[tuple[str, re.Pattern]] | None = None):
    """(hits, warnings, files read, files skipped)."""
    hits: list[str] = []
    warnings: list[str] = []
    personal = personal or []
    allowlist = allowlist or []
    infra = infra or []
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
                for marker in personal:
                    if marker in probe:
                        hits.append("%s: personal marker %r" % (where, marker))
            for rule_id, rule_re in infra:
                match = rule_re.search(probe)
                if match:
                    hits.append("%s: internal infra [%s] %r"
                                % (where, rule_id, match.group(0)))
            if AGENTS_RE.search(line):
                hits.append("%s: .agents path" % where)
            if PHASE_RE.search(line):
                hits.append("%s: 'Phase N'" % where)
            match = DECISION_RE.search(line)
            if match:
                hits.append("%s: decision id %r" % (where, match.group(0)))
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

    patterns: str = ""
    "Machine-local pattern file (default: $DOTAGENTS_AUDIT_PATTERNS, else the agent store's)."
    ("--patterns",)

    commits_only: bool = False
    "Scan only commit messages, skipping the tree."
    ("--commits-only",)

    def __call__(self) -> int:
        repo = Path(self.repo).resolve()
        print("repo:     %s" % repo)
        optout_state, optout_reason, optout_problems = self._optout(repo)

        if self.commits_only:
            return self._commit_messages(repo)

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
            personal, allowlist, infra)
        commit_hits: list[tuple[str, str]] = []
        commits = 0
        if is_git_worktree(repo):
            commit_hits, commits = scan_commits(repo)

        print("files:    %d listed, %d read, %d skipped (binary or unreadable)"
              % (len(files), read, skipped))
        print("patterns: .agents path + 'Phase N' + decision ids, "
              "%d plan basenames (%d strict, %d generic->warn), "
              "%d personal marker(s), %d infra rule(s)"
              % (len(distinctive) + len(generic), len(distinctive), len(generic),
                 len(personal), len(infra)))
        print("commits:  %d examined for session trailers" % commits)
        print()

        tag = "[ships-nowhere] " if optout_state == "honoured" else ""
        for line in hits:
            print("%s%s" % (tag, line))
        for sha, pattern in commit_hits:
            print("%scommit %s: session trailer %r" % (tag, sha, pattern))
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

    def _commit_messages(self, repo: Path) -> int:
        """`--commits-only`: the commit-message scan alone.

        For a repository that tracks text about the private notes on purpose,
        where the tree scan would drown the commit-message signal.
        """
        if not is_git_worktree(repo):
            print("mode:     CANNOT CHECK")
            print("\nCANNOT CHECK: not a git repository, so there are no commit "
                  "messages to scan: %s" % repo)
            return EXIT_CANNOT_CHECK
        hits, commits = scan_commits(repo)
        print("mode:     commit messages only (tree scan SKIPPED)")
        print("commits:  %d examined for session trailers" % commits)
        print()
        if commits == 0:
            print("CANNOT CHECK: 0 commits under %s" % repo)
            return EXIT_CANNOT_CHECK
        for sha, pattern in hits:
            print("commit %s: session trailer %r" % (sha, pattern))
        if hits:
            print("\nFAIL (%d hits) [commit messages only; the tree was NOT "
                  "scanned]" % len(hits))
            return EXIT_HITS
        print("\nPASS [commit messages only; the tree was NOT scanned]")
        return EXIT_OK

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
