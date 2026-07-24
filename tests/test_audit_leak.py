"""`audit` is ONE file that is both the tool and the command (D84).

`src/dotagents/_overlay/dotagents/cmds/audit.py` is a bundled discovered command
module: `dotagents audit` resolves to its `Audit` class, and the same file runs
directly (`python audit.py --root .`, dispatched through `duho.main`). There is no
wrapper class and no separate `tools/audit_config.py` -- one file, one argument
definition, one implementation.

Covered here:
  1. the file runs STANDALONE and PASSes on this repo (what CI invokes);
  2. running it standalone exposes its flags via duho (--root/--probe/--check-templates);
  3. it is DISCOVERED as the `audit` subcommand (there is no compiled cli/audit.py);
  4. `leak-check` is NOT in the repo (personal -- it lives in the user's private
     `.agents/`, D84).

Run from repo root: ``PYTHONPATH=src python -m pytest tests/test_audit_leak.py``.
"""

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
sys.path.insert(0, str(SRC))

AUDIT = SRC / "dotagents" / "_overlay" / "dotagents" / "cmds" / "audit.py"


def test_audit_ships_in_the_bundled_cmds_dir():
    """Its home is what makes it both shipped and discoverable."""
    assert AUDIT.is_file()


def test_audit_runs_standalone_and_passes():
    """`python audit.py --root <repo>` -- exactly what CI invokes."""
    proc = subprocess.run(
        [sys.executable, str(AUDIT), "--root", str(REPO)],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "PASS" in proc.stdout


def test_standalone_help_exposes_flags_via_duho():
    """__main__ dispatches through duho, so flags/help come from the class."""
    proc = subprocess.run(
        [sys.executable, str(AUDIT), "--help"], capture_output=True, text=True
    )
    out = proc.stdout + proc.stderr
    for flag in ("--root", "--probe", "--check-templates"):
        assert flag in out, "%s missing from standalone help" % flag


def test_audit_is_discovered_not_a_compiled_builtin():
    """`audit` resolves through command discovery; the old wrapper is gone."""
    from dotagents import cli

    assert not (SRC / "dotagents" / "cli" / "audit.py").exists()
    names = set()
    for command in cli._discover([]):
        name = getattr(command, "_parsername_", None) or getattr(command, "__name__", "")
        names.add(str(name))
    assert "audit" in names


def test_leak_check_is_not_in_the_repo():
    """leak-check is personal (D84): the user's private `.agents/`, not here."""
    assert not (REPO / "tools" / "leak_check.py").exists()
    assert not (SRC / "dotagents" / "cli" / "leak_check.py").exists()
