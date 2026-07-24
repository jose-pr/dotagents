"""`dotagents audit` dispatch tests (D70).

`audit` is a compiled built-in (`dotagents.cli.audit.Audit`) -- a thin front onto
the single standalone implementation `tools/audit_config.py`. There is no duplicate
logic in the CLI. These tests pin that:

  1. `dotagents audit --root .` dispatches and returns the SAME result as invoking
     the standalone `tools/audit_config.py --root .` directly (PASS, exit 0, on
     this repo);
  2. `dotagents audit --repo-hygiene .` dispatches and PASSES on this repo;
  3. the shared `_resolve_required_tool` resolver finds the repo-checkout tool.

leak-check was removed from the repo entirely (D84): it enforces personal plan-
naming conventions, so it moves to the user's private `.agents/cmds/` as a
discovered command module -- it is no longer built in and no longer tested here.

Run from repo root: ``PYTHONPATH=src python -m pytest tests/test_audit_leak.py``.
"""

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src"
sys.path.insert(0, str(SRC))

from dotagents.cli._common import _resolve_required_tool  # noqa: E402
from dotagents.cli.audit import Audit  # noqa: E402


def _standalone(tool: str, *args: str) -> int:
    return subprocess.call(
        [sys.executable, str(REPO / "tools" / tool), *args],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


# ---------------------------------------------------------------------------
# Resolver: the shared front-end helper finds the required tool.
# ---------------------------------------------------------------------------

def test_resolver_finds_repo_tool():
    resolved = _resolve_required_tool("audit_config.py")
    assert resolved is not None
    assert resolved.name == "audit_config.py"
    assert resolved.exists()


def test_resolver_prefers_explicit_root(tmp_path):
    tools = tmp_path / "tools"
    tools.mkdir()
    planted = tools / "audit_config.py"
    planted.write_text("# planted\n", encoding="utf-8")
    resolved = _resolve_required_tool("audit_config.py", root=tmp_path)
    assert resolved == planted


def test_resolver_missing_tool_returns_none():
    assert _resolve_required_tool("no_such_tool.py") is None


# ---------------------------------------------------------------------------
# `dotagents audit`: dispatches, and matches the standalone script.
# ---------------------------------------------------------------------------

def test_audit_matches_standalone():
    standalone_rc = _standalone("audit_config.py", "--root", str(REPO))
    assert standalone_rc == 0  # baseline: this repo PASSES

    cmd = Audit()
    cmd.root = REPO
    assert cmd() == standalone_rc


def test_audit_repo_hygiene_dispatches():
    cmd = Audit()
    cmd.root = REPO
    cmd.repo_hygiene = REPO
    # root audit + repo-hygiene both PASS on this repo -> combined rc 0.
    assert cmd() == 0
