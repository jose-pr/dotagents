"""`audit` is THIS REPO's CI tooling, not a dotagents command (D84 follow-up).

`tools/audit.py` validates the dotagents SOURCE REPO's layout -- every path in its
manifest is a repo path (`src/dotagents/_overlay/...`, `tools/...`). It is therefore
NOT a validator for an installed `~/.agents`, NOT a `dotagents` subcommand, and NOT
shipped in the package or the `.pyz`.

Covered here:
  1. it lives in `tools/` and runs standalone, PASSing on this repo (what CI does);
  2. running it standalone exposes its flags (--root/--probe/--check-templates);
  3. `audit` is NOT in the dotagents command surface;
  4. `leak-check` is not in the repo either (personal -- the user's private
     `.agents/`, D84).

Run from repo root: ``PYTHONPATH=src python -m pytest tests/test_audit_leak.py``.
"""

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
sys.path.insert(0, str(SRC))

AUDIT = REPO / "tools" / "audit.py"


def test_audit_lives_in_repo_tools():
    """It is repo CI tooling, beside cloud-setup.sh -- not inside the package."""
    assert AUDIT.is_file()
    assert not (SRC / "dotagents" / "_overlay" / "dotagents" / "cmds" / "audit.py").exists()


def test_audit_runs_standalone_and_passes():
    """`python tools/audit.py --root <repo>` -- exactly what CI invokes."""
    proc = subprocess.run(
        [sys.executable, str(AUDIT), "--root", str(REPO)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "PASS" in proc.stdout


def test_standalone_help_exposes_flags():
    """__main__ dispatches through duho, so flags/help come from the class."""
    proc = subprocess.run(
        [sys.executable, str(AUDIT), "--help"], capture_output=True, text=True
    )
    out = proc.stdout + proc.stderr
    for flag in ("--root", "--probe", "--check-templates"):
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


def test_leak_check_is_not_in_the_repo():
    """leak-check is personal (D84): the user's private `.agents/`, not here."""
    assert not (REPO / "tools" / "leak_check.py").exists()
    assert not (SRC / "dotagents" / "cli" / "leak_check.py").exists()
