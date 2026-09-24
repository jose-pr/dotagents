"""`audit` is THIS REPO's CI tooling, not a dotagents command (D84 follow-up).

`tools/audit.py` validates the dotagents SOURCE REPO's layout -- every path in its
manifest is a repo path (`src/dotagents/_overlay/...`, `tools/...`). It is therefore
NOT a validator for an installed `~/.agents`, NOT a `dotagents` subcommand, and NOT
shipped in the package or the `.pyz`.

Covered here:
  1. it lives in `tools/` and runs standalone, PASSing on this repo (what CI does);
  2. running it standalone exposes its flags (--root/--overlays/--probe/--check-templates);
  3. `audit` is NOT in the dotagents command surface;
  4. no personal scanning tool is in the repo either -- `tools/` holds exactly
     the repo-tooling files, and personal command modules live in the
     user's private `.agents/` (D84);
  5. its checks can fail: a forbidden pattern, an unresolved overlay `rules`
     path, and a template check with no overlays to check all exit 1.

Run from repo root: ``PYTHONPATH=src python -m pytest tests/test_audit_leak.py``.
"""

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
sys.path.insert(0, str(SRC))

AUDIT = REPO / "tools" / "audit.py"


def _audit(*args):
    return subprocess.run(
        [sys.executable, str(AUDIT), *args], capture_output=True, text=True
    )


def test_audit_lives_in_repo_tools():
    """It is repo CI tooling, beside cloud-setup.sh -- not inside the package."""
    assert AUDIT.is_file()
    assert not (SRC / "dotagents" / "_overlay" / "dotagents" / "cmds" / "audit.py").exists()


def test_audit_runs_standalone_and_passes():
    """`python tools/audit.py --root <repo>` -- exactly what CI invokes."""
    proc = _audit("--root", str(REPO))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "PASS" in proc.stdout


def test_standalone_help_exposes_flags():
    """__main__ dispatches through duho, so flags/help come from the class."""
    proc = _audit("--help")
    out = proc.stdout + proc.stderr
    for flag in ("--root", "--overlays", "--probe", "--check-templates"):
        assert flag in out, "%s missing from standalone help" % flag


def test_audit_is_not_a_dotagents_command():
    """A user of dotagents gets no `audit` -- it only validates THIS repo."""
    from dotagents import cli

    assert not (SRC / "dotagents" / "cli" / "audit.py").exists()
    names = set()
    for command in cli._discover([]):
        name = getattr(command, "_parsername_", None) or getattr(command, "__name__", "")
        names.add(str(name))
    assert "audit" not in names


def test_no_personal_tooling_is_in_the_repo():
    """Personal tooling lives in the user's private `.agents/` (D84), not here:
    `tools/` is exactly the two repo-tooling files, and the CLI package ships
    only its known modules."""
    assert sorted(p.name for p in (REPO / "tools").iterdir()) == ["audit.py", "cloud-setup.sh"]
    cli_modules = sorted(p.name for p in (SRC / "dotagents" / "cli").glob("*.py"))
    assert cli_modules == [
        "__init__.py", "_common.py", "about.py", "build_pyz.py", "context.py", "env.py",
        "init.py", "overlays.py",
    ]


def test_probe_with_a_forbidden_pattern_fails(tmp_path):
    probe = tmp_path / "probe.md"
    probe.write_text("see file:///" + "~/notes\n", encoding="utf-8")
    proc = _audit("--root", str(REPO), "--probe", str(probe))
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "FORBIDDEN" in proc.stdout


def _overlay(root, name, manifest, rules=()):
    d = root / name
    d.mkdir(parents=True)
    (d / "overlay.toml").write_text(manifest, encoding="utf-8")
    for rel in rules:
        (d / rel).parent.mkdir(parents=True, exist_ok=True)
        (d / rel).write_text("- **Rule**: text\n", encoding="utf-8")


def test_unresolved_overlay_rules_paths_fail(tmp_path):
    """Read with the CLI's manifest reader: a single-quoted entry and an
    entry after a `]` inside a comment are both seen, and both reported."""
    ov = tmp_path / "overlays"
    _overlay(ov, "a", "rules = ['rules/MISSING.md']\n")
    _overlay(ov, "b", 'rules = [ # see [docs]\n  "rules/ALSO_MISSING.md",\n]\n')
    _overlay(ov, "good", 'rules = ["rules/OK.md"]\n', rules=["rules/OK.md"])
    proc = _audit("--root", str(REPO), "--overlays", str(ov))
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "rules/MISSING.md" in proc.stdout
    assert "rules/ALSO_MISSING.md" in proc.stdout
    assert "overlays/good" not in proc.stdout


def test_resolved_overlay_rules_pass(tmp_path):
    ov = tmp_path / "branch" / "overlays"  # a `repo` branch checkout layout
    _overlay(ov, "good", 'rules = ["rules/OK.md"]\n', rules=["rules/OK.md"])
    proc = _audit("--root", str(REPO), "--overlays", str(tmp_path / "branch"))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "manifests of 1 overlays" in proc.stdout


def test_check_templates_without_overlays_is_not_a_pass(tmp_path):
    proc = _audit("--check-templates", "--root", str(tmp_path))
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "NOT CHECKED" in proc.stdout


def test_repo_gitignore_has_what_the_template_check_requires():
    """The standard this repo's audit holds the reference .gitignore to."""
    text = (REPO / ".gitignore").read_text(encoding="utf-8")
    for needle in ("\n.agents\n", "*.local.*", "CLAUDE*", ".claude"):
        assert needle in text, needle
