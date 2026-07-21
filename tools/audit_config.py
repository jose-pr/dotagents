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
    "overlays/agents/antigravity.md",
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
    "overlays/node/references/workflows/node/test.yaml",
    "overlays/node/references/workflows/node/release.yaml",
    "overlays/rust/references/workflows/rust/test.yaml",
    "overlays/rust/references/workflows/rust/release.yaml",
]

BASE_PATTERNS = ["file:///" + "~"]
REF_PATTERNS = BASE_PATTERNS + ["pathlib" + "_next", "C:\\" + "Users", "jo" + "se"]
# Concatenated so this file never matches itself.
# Public PyPI package names are NOT personal leftovers — they may appear in tracked
# docs/logs by name. Only truly private markers stay below: user accounts, machine
# paths, and un-published project names. (pathlib_next, duho, yaconfiglib, pydhcp are
# published — deliberately omitted.) Concatenated so this file never matches itself.
PERSONAL_PATTERNS = ["C:\\" + "Users", "C:/" + "Users", "~/" + "devel/",
                     "jo" + "se", "ala" + "can",
                     "proxy" + "lib", "pytrue" + "nas"]

# Public identities that legitimately appear in tracked files (the published GitHub
# org the packages ship under). Neutralized before pattern matching so e.g. the
# `github.com/<org>` URL in pyproject.toml doesn't trip the bare-username pattern,
# while a real leak like a Windows user-profile path still matches. Concatenated so this
# file never matches itself.
PUBLIC_ALLOWLIST = ["jo" + "se-pr"]

BUDGETS = {"src/dotagents/_overlay/AGENTS.md": 2500,
           "overlays/flows/flows/PLAN.md": 3000, "overlays/flows/flows/EXEC.md": 3000,
           "overlays/flows/flows/REVIEW.md": 3000, "overlays/flows/flows/REPO.md": 3000}

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
        ck(".gitignore", lambda: has(".gitignore", ["AGENTS.md", ".agents/", "CLAUDE*", ".claude"]))
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
