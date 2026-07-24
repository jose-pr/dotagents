#!/usr/bin/env python3
"""Audit THIS REPO's structure. CI tooling -- not a shipped dotagents feature.

Every path this checks is a path in the dotagents SOURCE REPO
(`src/dotagents/_overlay/...`, `tools/...`) and the size budget sizes a repo file.
It validates that this repo still ships what it is supposed to ship. It is **not**
a validator for an installed `~/.agents` config -- run against one, everything in
the manifest would be "missing", because an installed config has no `src/` tree.

So it deliberately lives in `tools/` (repo CI tooling, like `cloud-setup.sh`) and
is **not** a `dotagents` subcommand, not bundled in the package, and not shipped in
the `.pyz`: a user of dotagents has no use for it.

Personal-leak / hygiene scanning (machine paths, usernames, private repo names) is
NOT this tool's job (D84) -- that is `leak-check`, a personal command in the user's
private `.agents/`, run locally before a push.

Usage (what CI runs):
  python tools/audit.py --root .                    existence + forbidden-pattern
                                                    (BASE_PATTERNS) scan + sizes
  python tools/audit.py --check-templates --root .  instantiate + parse-check the
                                                    reference templates (3.11+)
  python tools/audit.py --probe <path> --root .     add one file to the manifest
                                                    (negative tests)

Exit 1 on missing manifest files, forbidden patterns, or failed template checks.
Size budgets only warn. Scope is a closed manifest -- never a tree walk.
"""
import re
import sys
from pathlib import Path
from typing import Optional

# This audits the REPO, so the default root is the repo (this file is tools/audit.py,
# so parents[1] is the checkout root) -- not `~/.agents`, which has no `src/` tree and
# would report every manifest entry missing.
DEFAULT_ROOT = Path(__file__).resolve().parents[1]

# Manifest paths are relative to a repo checkout root (--root .). The config is
# now a base overlay (src/dotagents/_overlay) plus opt-in example overlays; the
# required tooling lives at top-level tools/.
#
# The example overlays themselves moved to a separate `overlays` orphan branch
# (origin/overlays) -- main's tree is overlays-free (see D77). So the manifest
# here scopes to what main actually ships: the base overlay + required tooling.
# The overlays branch carries its own copy of the example content and its tests;
# validating that content is the overlays branch's concern, not main's.
SCAN = [
    "src/dotagents/_overlay/AGENTS.md", "src/dotagents/_overlay/CLAUDE.md",
    "src/dotagents/_overlay/dotagents/DECISIONS.md",
]
REFS = []
# leak_check.py is no longer a required tool of main: it moved to the opt-in
# `leak-check` overlay as a command module (D84), so main's tree no longer ships
# it and it is not in this manifest.
# The auditor itself now ships as the bundled `audit` COMMAND MODULE (this file):
# `src/dotagents/_overlay/dotagents/cmds/audit.py`. It is both the tool (run it
# directly) and the `dotagents audit` command, so there is no separate
# tools/audit_config.py. leak_check.py is personal (D84) and not shipped here.
EXIST_ONLY = [
    "tools/audit.py",
    "tools/cloud-setup.sh",
]
# Example-overlay files live on the `overlays` branch, not in main's tree, so the
# repo checkout (--root .) has no overlays/ dir to enumerate. Kept empty here and
# guarded below (checked only when an overlays/ dir is present -- e.g. a CI job
# that checks the overlays branch out into ./overlays for integration testing).
EXAMPLES = []

# Generic, structural forbidden patterns only (D84). No personal/machine markers
# live here -- that is leak-check's concern, run locally, from the user's private
# `.agents/`. `file:///~` is a broken tilde-in-file-URI that should never ship.
BASE_PATTERNS = ["file:///" + "~"]
REF_PATTERNS = BASE_PATTERNS

# The base overlay's AGENTS.md is the only always-loaded file main ships, so it
# is the one with a size budget here. The flow files (PLAN/EXEC/REVIEW/REPO) and
# their budgets live with the overlay content on the `overlays` branch (D77).
BUDGETS = {"src/dotagents/_overlay/AGENTS.md": 2500}

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
    # The reference/language templates moved to the `overlays` branch (D77), so a
    # plain main checkout has no overlays/ dir to instantiate. Skip cleanly rather
    # than fail -- CI checks the templates on the overlays branch (or after checking
    # it out into ./overlays), where the source files actually live.
    if not (root / "overlays").is_dir():
        print("SKIP --check-templates: no overlays/ dir "
              "(templates live on the `overlays` branch)")
        return []
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


# --------------------------------------------------------------------------- #
# Command surface. This module IS the tool AND the `dotagents audit` command:
# it is a discovered command module (D76/D84) in the bundled `dotagents/cmds/`
# dir, so `dotagents audit` resolves to the class below -- there is no separate
# wrapper shelling out to a script. Run it directly too (`python audit.py
# --root .`): `__main__` dispatches through duho, so BOTH paths share one
# argument definition, one help text, one implementation.
#
# Everything above this line is stdlib-only, so the audit logic itself carries
# no package/duho dependency; only the command surface does.
# --------------------------------------------------------------------------- #

from duho import Cmd, LoggingArgs  # noqa: E402


class Audit(LoggingArgs, Cmd):
    """Audit dotagents-config structure (manifest, forbidden patterns, budgets).

    Structural only -- personal-leak/hygiene scanning is `leak-check`'s job (D84).
    """

    _parsername_ = "audit"

    root: Path = DEFAULT_ROOT
    "Config tree to audit (default ~/.agents; a checkout: --root .)."
    ("--root",)

    probe: Optional[Path] = None
    "Add one extra file to the scan manifest (negative tests)."
    ("--probe",)

    check_templates_: bool = False
    "Instantiate references/ templates in a temp dir and parse-check them (3.11+)."
    ("--check-templates",)

    def __call__(self) -> int:
        root = Path(self.root).expanduser().resolve()
        if self.check_templates_:
            failures = check_templates(root)
        else:
            failures = audit(root, Path(self.probe) if self.probe else None)
        if failures:
            print("FAIL")
            for f in failures:
                print("  " + f)
            return 1
        print("PASS")
        return 0


if __name__ == "__main__":
    import duho

    sys.exit(duho.main(Audit, sys.argv[1:]))
