#!/usr/bin/env python3
"""Audit THIS REPO's structure. CI tooling -- not a shipped dotagents feature.

Every path this checks is a path in the dotagents SOURCE REPO
(`src/dotagents/_overlay/...`, `tools/...`) or in a checkout of the `repo`
branch, which holds the example overlays under `overlays/<name>/`. It is **not**
a validator for an installed `~/.agents` config, which has no `src/` tree.

So it lives in `tools/` (repo CI tooling, like `cloud-setup.sh` and
`pyz_smoke.sh`) and is **not** a `dotagents` subcommand, not bundled in the
package, and not shipped in the `.pyz`: a user of dotagents has no use for it.

Personal-leak / hygiene scanning (machine paths, usernames, private repo names) is
NOT this tool's job (D84) -- that is a personal command module in the user's
private `.agents/`, run locally before a push.

Usage (what CI runs):
  python tools/audit.py --root . [--overlays DIR]
      the required files exist; every file of the base overlay
      (src/dotagents/_overlay/) is free of the forbidden patterns; size budget
      (warning only); and, when an overlays dir is found, every `rules` path an
      overlay.toml declares resolves to a rules file.
  python tools/audit.py --check-templates --root . [--overlays DIR]
      instantiate + parse-check the overlays' reference templates (3.11+).
      Without an overlays dir it fails with NOT CHECKED.
  python tools/audit.py --probe <path> --root .
      scan one extra file too (negative tests)

The overlays dir is --overlays DIR, else the first of <root>/overlays,
<root>/repo and <root>/overlays-src that holds overlays, either directly
(<dir>/<name>/overlay.toml) or as a `repo` branch checkout
(<dir>/overlays/<name>/overlay.toml).

Exit 1 on missing required files, forbidden patterns, unresolved rules paths, or
failed (or unrunnable) template checks.
"""
import sys
from pathlib import Path
from typing import List, Optional

# This audits the REPO, so the default root is the repo (this file is tools/audit.py,
# so parents[1] is the checkout root) -- not `~/.agents`, which has no `src/` tree.
DEFAULT_ROOT = Path(__file__).resolve().parents[1]

# The overlay manifest check reads overlay.toml with the CLI's own reader, so it
# can never disagree with what `overlays add` does. Prefer this checkout's copy
# of the package over any installed one.
sys.path.insert(0, str(DEFAULT_ROOT / "src"))

#: The base overlay `init` lays down. Every file under it is scanned.
BASE_OVERLAY = "src/dotagents/_overlay"

#: Files main must ship: the base overlay's template, bundled command modules and
#: hook scripts, and this repo's CI tooling.
REQUIRED = [
    "src/dotagents/_overlay/dotagents/templates/AGENTS.md",
    "src/dotagents/_overlay/dotagents/templates/PROJECT.md",
    "src/dotagents/_overlay/dotagents/cmds/findings.py",
    "src/dotagents/_overlay/dotagents/cmds/launch.py",
    "src/dotagents/_overlay/dotagents/hooks/preinvocation_antigravity_context.py",
    "src/dotagents/_overlay/dotagents/hooks/pretooluse_codex_env.py",
    "src/dotagents/_overlay/dotagents/hooks/sessionstart_codex_context.py",
    "tools/audit.py",
    "tools/cloud-setup.sh",
    "tools/pyz_smoke.sh",
]

# Generic, structural forbidden patterns only (D84). No personal/machine markers
# live here -- that is the personal scanner's concern, run locally, from the user's
# private `.agents/`. `file:///~` is a broken tilde-in-file-URI that should never ship.
BASE_PATTERNS = ["file:///" + "~"]

# The base overlay's AGENTS.md is the only always-loaded file main ships, so it
# is the one with a size budget here.
BUDGETS = {"src/dotagents/_overlay/dotagents/templates/AGENTS.md": 2500}

SUBST = {"<project_name>": "demopkg", "<gh_org>": "demoorg",
         "<package_name>": "demopkg", "<year>": "2026",
         "<copyright_holder>": "Demo", "<msrv>": "1.70"}


def _holds_overlays(path: Path) -> bool:
    return any(path.glob("*/overlay.toml"))


def find_overlays(root: Path, overlays: Optional[Path] = None) -> Optional[Path]:
    """The directory holding `<name>/overlay.toml` entries, or None.

    `overlays` (the --overlays flag) is the only candidate when given; a
    `repo` branch checkout is accepted in place of its `overlays/` subdir."""
    candidates = [overlays] if overlays is not None else [
        root / "overlays", root / "repo", root / "overlays-src",
    ]
    for cand in candidates:
        for path in (cand, cand / "overlays"):
            if path.is_dir() and _holds_overlays(path):
                return path
    return None


def _scan(path: Path, rel: str, failures: List[str]) -> None:
    text = path.read_text(encoding="utf-8", errors="replace")
    for pat in BASE_PATTERNS:
        if pat in text:
            failures.append("FORBIDDEN %r in %s" % (pat, rel))


def audit(root: Path, probe: Optional[Path] = None,
          overlays: Optional[Path] = None) -> List[str]:
    print("root: %s" % root)
    failures: List[str] = []
    for rel in REQUIRED:
        if not (root / rel).is_file():
            failures.append("MISSING: %s" % rel)

    base = root / BASE_OVERLAY
    scanned = [p for p in sorted(base.rglob("*"))
               if p.is_file() and "__pycache__" not in p.parts]
    for path in scanned:
        _scan(path, path.relative_to(root).as_posix(), failures)
    print("scanned %d files under %s" % (len(scanned), BASE_OVERLAY))
    if probe is not None:
        if probe.is_file():
            _scan(probe, str(probe), failures)
        else:
            failures.append("MISSING: %s" % probe)

    for rel, budget in BUDGETS.items():
        path = root / rel
        if path.is_file() and path.stat().st_size > budget:
            print("WARN: %s is %dB (budget %dB)" % (rel, path.stat().st_size, budget))

    ov = find_overlays(root, overlays)
    if ov is None and overlays is not None:
        failures.append("MISSING: no overlays under --overlays %s" % overlays)
    elif ov is None:
        print("NOT CHECKED overlay manifests: no overlays dir found "
              "(pass --overlays DIR, a checkout of the `repo` branch)")
    else:
        failures += check_overlay_manifests(ov)
    return failures


def check_overlay_manifests(overlays_dir: Path) -> List[str]:
    """Every `rules` path in an overlay.toml must resolve to a rules file.

    A typo there silently drops an always-on rule from every install. Read with
    the CLI's own manifest reader and rules extractor, so this check sees exactly
    what `overlays add` would merge."""
    from dotagents._overlays import Overlay

    failures: List[str] = []
    found = Overlay.discover(overlays_dir)
    for overlay in found:
        rules = overlay.read_manifest()["rules"]
        _blocks, warnings = overlay.rule_blocks(rules)
        failures += ["overlays/%s: %s" % (overlay.name, w) for w in warnings]
    print("checked the manifests of %d overlays in %s" % (len(found), overlays_dir))
    return failures


def check_templates(root: Path, overlays: Optional[Path] = None) -> List[str]:
    ov = find_overlays(root, overlays)
    if ov is None:
        return ["NOT CHECKED: no overlays dir found; the templates live on the "
                "`repo` branch (pass --overlays DIR)"]
    if sys.version_info < (3, 11):
        return ["NOT CHECKED: --check-templates needs Python 3.11+ (tomllib)"]
    import json
    import shutil
    import tempfile
    import tomllib
    print("overlays: %s" % ov)
    failures: List[str] = []
    tmp = Path(tempfile.mkdtemp(prefix="agents_tpl_"))
    try:
        refs_dir = ov / "engineering" / "references"
        sources = [(refs_dir / n, n) for n in
                   ["README.md", "CHANGELOG.md", ".gitignore", "docs-index.md"]]
        sources += [
            (ov / "python" / "references" / "mkdocs.yml", "mkdocs.yml"),
            (ov / "node" / "references" / "package.json", "package.json"),
            (ov / "python" / "references" / "pyproject.toml", "pyproject.toml"),
            (ov / "rust" / "references" / "Cargo.toml", "Cargo.toml"),
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
        # .agents (D55: `dotagents link-project` makes it a symlink, which a
        # directory-only pattern would not match).
        ck(".gitignore", lambda: has(".gitignore",
                                     ["\n.agents\n", "*.local.*", "CLAUDE*", ".claude"]))
        ck("docs-index.md", lambda: has("docs-index.md", ["#"]))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return failures


# --------------------------------------------------------------------------- #
# Command surface. This module is repo CI tooling, NOT a `dotagents` command
# (it is not in the bundled `dotagents/cmds/` dir and never ships in the
# package -- `tests/test_audit_leak.py` pins that). It is run directly
# (`python tools/audit.py --root .`); `__main__` dispatches through duho for
# the argument definition and help text, and the manifest check reads
# overlay.toml with dotagents' own reader, so the CI job that runs it installs
# the package (`pip install -e .`) first.
# --------------------------------------------------------------------------- #

from duho import Cmd, LoggingArgs  # noqa: E402


class Audit(LoggingArgs, Cmd):
    """Audit the dotagents repo's structure (required files, forbidden patterns,
    overlay manifests, reference templates).

    Structural only -- personal-leak/hygiene scanning is a personal tool's job (D84).
    """

    _parsername_ = "audit"

    root: Path = DEFAULT_ROOT
    "Repo checkout to audit (default: this checkout's root)."
    ("--root",)

    overlays: Optional[Path] = None
    (
        "Directory of example overlays (a checkout of the `repo` branch, or its "
        "overlays/ dir). Default: the first of <root>/overlays, <root>/repo, "
        "<root>/overlays-src that holds overlays."
    )
    ("--overlays",)

    probe: Optional[Path] = None
    "Scan one extra file for forbidden patterns (negative tests)."
    ("--probe",)

    check_templates_: bool = False
    "Instantiate the overlays' reference templates in a temp dir and parse-check them (3.11+)."
    ("--check-templates",)

    def __call__(self) -> int:
        root = Path(self.root).expanduser().resolve()
        overlays = Path(self.overlays).expanduser().resolve() if self.overlays else None
        if self.check_templates_:
            failures = check_templates(root, overlays)
        else:
            failures = audit(root, Path(self.probe) if self.probe else None, overlays)
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
