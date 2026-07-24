#!/usr/bin/env python3
"""Audit agent-config integrity. Default mode runs on Python 3.9+.

Modes:
  (default)              existence + forbidden-pattern scan + size table
  --root <path>          audit this tree instead of ~/.agents (e.g. a checkout: .)
  --probe <path>         add one extra file to the scan manifest (negative tests)
  --check-templates      instantiate references/ templates in a temp dir and
                         parse-check them (requires Python 3.11+ for tomllib)
  --repo-hygiene <repo>  scan every git-tracked file in <repo> for personal
                         leftovers (user accounts, machine paths, private repo
                         names) — for repos that intentionally track agent config

Exit 1 on missing manifest files, forbidden patterns, or failed template checks.
Size budgets only warn. Scope is a closed manifest -- never a tree walk (the tree
also holds harness state, backups, and private plans that legitimately contain
the forbidden strings).
"""
import re
import sys
from pathlib import Path

DEFAULT_ROOT = Path.home() / ".agents"

# Manifest paths are relative to a repo checkout root (--root .). The config is
# now a base overlay (src/dotagents/_overlay) plus opt-in overlays/<name>/; the
# required tooling lives at top-level tools/.
SCAN = [
    "src/dotagents/_overlay/AGENTS.md", "src/dotagents/_overlay/CLAUDE.md",
    "src/dotagents/_overlay/dotagents/DECISIONS.md",
    "overlays/flows/flows/PLAN.md", "overlays/flows/flows/EXEC.md",
    "overlays/flows/flows/REVIEW.md", "overlays/flows/flows/REPO.md",
    "overlays/flows/MODELS.md",
    "overlays/recovery/kb/RECOVERY.md",
]
REFS = [
    "overlays/references/references/README.md",
    "overlays/references/references/CHANGELOG.md",
    "overlays/references/references/LICENSE",
    "overlays/references/references/.gitignore",
    "overlays/references/references/docs-index.md",
    "overlays/references/references/master_refactoring_plan.md",
]
EXIST_ONLY = ["tools/audit_config.py", "tools/leak_check.py", "tools/cloud-setup.sh"]
# The language/agent overlays -- opt-in at install time, but a repo checkout
# (--root .) should have every example present. Checked only when overlays/ exists.
EXAMPLES = [
    "overlays/private-sync/kb/PRIVATE_SYNC.md",
    "overlays/private-sync/hooks/private-sync-start.sh",
    "overlays/private-sync/hooks/private-sync-stop.sh",
    "overlays/private-sync/hooks/_agents-git-auth.sh",
    "overlays/private-sync/hooks/settings.snippet.json",
    "overlays/python/kb/PYTHON.md", "overlays/node/kb/NODE.md", "overlays/rust/kb/RUST.md",
    "overlays/node/references/package.json", "overlays/python/references/pyproject.toml",
    "overlays/rust/references/Cargo.toml", "overlays/python/references/mkdocs.yml",
    "overlays/python/references/workflows/python/test.yml",
    "overlays/python/references/workflows/python/release.yml",
    "overlays/python/references/workflows/python/docs.yml",
    "overlays/node/references/workflows/node/test.yaml",
    "overlays/node/references/workflows/node/release.yaml",
    "overlays/node/references/workflows/node/docs.yaml",
    "overlays/rust/references/workflows/rust/test.yaml",
    "overlays/rust/references/workflows/rust/release.yaml",
    "overlays/rust/references/workflows/rust/docs.yaml",
    "overlays/net/kb/NET.md",
    "overlays/net/bin/curl.py", "overlays/net/bin/curl.cmd",
    "overlays/net/lib/certifi/__init__.py", "overlays/net/lib/certifi/__main__.py",
    "overlays/net/lib/VENDORED.md",
    "overlays/net/lib/httplib/__init__.py", "overlays/net/lib/httplib/proxy.py",
    "overlays/net/lib/httplib/jar.py", "overlays/net/lib/httplib/cookies.py",
    "overlays/net/lib/httplib/auth.py", "overlays/net/lib/httplib/warnings.py",
    "overlays/net/lib/httplib/session.py", "overlays/net/lib/httplib/fetch.py",
    "overlays/net/setup.py",
]

BASE_PATTERNS = ["file:///" + "~"]

# Machine-path patterns are universal; who you are is not. Personal markers
# (usernames, real names, un-published project names) belong to the person
# running this, not to a public repo's tooling -- so they load from
# `~/.agents/audit_patterns.local.json` (override with $DOTAGENTS_AUDIT_PATTERNS),
# which `*.local.*` already keeps out of every repo.
#
# Shape -- every key optional, each a list of strings:
#   {"personal": ["..."], "public_allowlist": ["..."], "refs": ["..."]}
#
# `public_allowlist` entries are blanked before matching, so a published org name
# in a URL does not trip a bare-username pattern while a real user-profile path
# still does. Published package names are NOT personal leftovers and must not be
# listed -- they legitimately appear in tracked docs.
# Deliberately NOT "/home/": generic docs legitimately use `/home/user/...` as an
# example path, so it flags prose rather than leaks.
_DEFAULT_PERSONAL = ["C:\\" + "Users", "C:/" + "Users", "~/" + "devel/"]


def _load_local_patterns():
    """Read the machine-local pattern file, if any. Never fails the run."""
    import json
    import os

    raw = os.environ.get("DOTAGENTS_AUDIT_PATTERNS")
    path = Path(raw) if raw else Path.home() / ".agents" / "audit_patterns.local.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        sys.stderr.write("warning: could not read %s (%s); "
                         "using built-in patterns only\n" % (path, exc))
        return {}
    return data if isinstance(data, dict) else {}


_LOCAL = _load_local_patterns()
PERSONAL_PATTERNS = _DEFAULT_PERSONAL + list(_LOCAL.get("personal", []))
PUBLIC_ALLOWLIST = list(_LOCAL.get("public_allowlist", []))
REF_PATTERNS = BASE_PATTERNS + _DEFAULT_PERSONAL + list(_LOCAL.get("refs", []))

# REPO.md is deliberately larger than the single-topic flow files: it covers
# layout + CI + six meta files + versioning where PLAN/EXEC/REVIEW each cover
# one topic (D71 — re-base, not split; a further trim would delete rules, not
# prose). Its budget is set from the tool's own measured size with headroom.
BUDGETS = {"src/dotagents/_overlay/AGENTS.md": 2500,
           "overlays/flows/flows/PLAN.md": 3000, "overlays/flows/flows/EXEC.md": 3000,
           "overlays/flows/flows/REVIEW.md": 3000, "overlays/flows/flows/REPO.md": 7000}

SUBST = {"<project_name>": "demopkg", "<gh_org>": "demoorg",
         "<package_name>": "demopkg", "<year>": "2026",
         "<copyright_holder>": "Demo", "<msrv>": "1.70"}


def audit(root, probe=None):
    print("root: %s" % root)
    failures, table = [], []
    jobs = [(p, BASE_PATTERNS) for p in SCAN] + [(p, REF_PATTERNS) for p in REFS] \
        + [(p, None) for p in EXIST_ONLY]
    if (root / "overlays").is_dir():
        jobs += [(p, REF_PATTERNS) for p in EXAMPLES]
    if probe:
        jobs.append((probe, BASE_PATTERNS))
    for rel, patterns in jobs:
        path = Path(rel) if probe and rel is probe else root / rel
        if not path.is_file():
            failures.append("MISSING: %s" % rel)
            continue
        size = path.stat().st_size
        table.append("%-55s%7d" % (rel, size))
        if patterns:
            text = path.read_text(encoding="utf-8", errors="replace")
            for pat in patterns:
                if pat in text:
                    failures.append("FORBIDDEN %r in %s" % (pat, rel))
        if BUDGETS.get(rel) and size > BUDGETS[rel]:
            print("WARN: %s is %dB (budget %dB)" % (rel, size, BUDGETS[rel]))
    print("\n".join(table))
    failures += _check_overlay_manifests(root)
    return failures


def _check_overlay_manifests(root):
    """Every `rules` path in an overlay.toml must exist.

    A typo there silently drops an always-on rule from every install -- the exact
    failure that let three rules live only in the install target and nowhere in
    source. Cheap to catch here, invisible otherwise."""
    failures = []
    overlays = root / "overlays"
    if not overlays.is_dir():
        return failures
    for manifest in sorted(overlays.glob("*/overlay.toml")):
        text = manifest.read_text(encoding="utf-8", errors="replace")
        block = re.search(r"(?ms)^rules\s*=\s*\[(.*?)\]", text)
        if not block:
            continue
        for rel in re.findall(r'"([^"\n]+)"', block.group(1)):
            if not (manifest.parent / rel).is_file():
                failures.append(
                    "MISSING rules file %r declared by %s"
                    % (rel, manifest.relative_to(root).as_posix())
                )
    return failures


def check_templates(root):
    if sys.version_info < (3, 11):
        sys.stderr.write("--check-templates needs Python 3.11+ (tomllib); "
                         "run via: py -3.12\n")
        return ["python too old for --check-templates"]
    import json
    import shutil
    import tempfile
    import tomllib
    failures = []
    tmp = Path(tempfile.mkdtemp(prefix="agents_tpl_"))
    try:
        refs_dir = root / "overlays" / "references" / "references"
        sources = [(refs_dir / n, n) for n in
                   ["README.md", "CHANGELOG.md", ".gitignore", "docs-index.md"]]
        sources += [
            (root / "overlays" / "python" / "references" / "mkdocs.yml", "mkdocs.yml"),
            (root / "overlays" / "node" / "references" / "package.json", "package.json"),
            (root / "overlays" / "python" / "references" / "pyproject.toml", "pyproject.toml"),
            (root / "overlays" / "rust" / "references" / "Cargo.toml", "Cargo.toml"),
        ]
        for src, name in sources:
            text = src.read_text(encoding="utf-8")
            lines = [ln for ln in text.splitlines()
                     if "<!-- EXECUTOR:" not in ln]
            text = "\n".join(lines) + "\n"
            for k, v in SUBST.items():
                text = text.replace(k, v)
            (tmp / name).write_text(text, encoding="utf-8")

        def ck(name, fn):
            try:
                fn()
                print("PASS %s" % name)
            except Exception as exc:  # noqa: BLE001 - report, don't crash
                failures.append("%s: %s" % (name, exc))
                print("FAIL %s: %s" % (name, exc))

        ck("pyproject.toml", lambda: tomllib.loads((tmp / "pyproject.toml").read_text(encoding="utf-8")))
        ck("Cargo.toml", lambda: tomllib.loads((tmp / "Cargo.toml").read_text(encoding="utf-8")))
        ck("package.json", lambda: json.loads((tmp / "package.json").read_text(encoding="utf-8")))

        def has(name, needles):
            text = (tmp / name).read_text(encoding="utf-8")
            missing = [n for n in needles if n not in text]
            if missing:
                raise AssertionError("missing %s" % missing)

        ck("mkdocs.yml", lambda: has("mkdocs.yml", ["theme:", "material", "mkdocstrings", "docs_dir: docs"]))
        ck("README.md", lambda: has("README.md", ["img.shields.io", "## Install", "Optional", "## Development", "## License"]))
        ck("CHANGELOG.md", lambda: has("CHANGELOG.md", ["[Unreleased]", "## [", "]: http"]))
        # No "AGENTS.md" (D54: no repo-root one) and no trailing slash on
        # .agents (D55/1c9bf7c: `dotagents link` makes it a symlink, which a
        # directory-only pattern would not match).
        ck(".gitignore", lambda: has(".gitignore",
                                     ["\n.agents\n", "*.local.md", "CLAUDE*", ".claude"]))
        ck("docs-index.md", lambda: has("docs-index.md", ["#"]))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return failures


def repo_hygiene(repo):
    import subprocess
    out = subprocess.run(["git", "-C", str(repo), "ls-files", "-z"],
                         capture_output=True, check=True)
    tracked = [f for f in out.stdout.decode("utf-8").split("\0") if f]
    print("repo: %s (%d tracked files)" % (repo, len(tracked)))
    failures = []
    for rel in tracked:
        path = repo / rel
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for allowed in PUBLIC_ALLOWLIST:
            text = text.replace(allowed, "")
        for pat in PERSONAL_PATTERNS:
            if pat in text:
                failures.append("PERSONAL %r in %s" % (pat, rel))
    return failures


def main(argv):
    root = DEFAULT_ROOT
    if "--root" in argv:
        root = Path(argv[argv.index("--root") + 1]).resolve()
    probe = None
    if "--probe" in argv:
        probe = Path(argv[argv.index("--probe") + 1])
    if "--repo-hygiene" in argv:
        failures = repo_hygiene(
            Path(argv[argv.index("--repo-hygiene") + 1]).resolve())
    elif "--check-templates" in argv:
        failures = check_templates(root)
    else:
        failures = audit(root, probe)
    if failures:
        print("FAIL")
        for f in failures:
            print("  " + f)
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
