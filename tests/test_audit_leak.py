"""`dotagents audit` / `dotagents leak-check` dispatch tests (D70).

Both subcommands are thin fronts onto the single standalone implementations
under `tools/` (`audit_config.py`, `leak_check.py`) -- there is no duplicate
logic in the CLI. These tests pin that:

  1. `dotagents audit --root .` dispatches and returns the SAME result as
     invoking the standalone `tools/audit_config.py --root .` directly (PASS,
     exit 0, on this repo);
  2. `dotagents leak-check <repo>` dispatches to `tools/leak_check.py` and
     returns its exit code;
  3. the shared `_resolve_required_tool` resolver finds the repo-checkout tool.

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
from dotagents.cli.leak_check import LeakCheck  # noqa: E402


def _standalone(tool: str, *args: str) -> int:
    return subprocess.call(
        [sys.executable, str(REPO / "tools" / tool), *args],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


# ---------------------------------------------------------------------------
# Resolver: the shared front-end helper finds the required tools.
# ---------------------------------------------------------------------------

def test_resolver_finds_repo_tools():
    for name in ("audit_config.py", "leak_check.py"):
        resolved = _resolve_required_tool(name)
        assert resolved is not None
        assert resolved.name == name
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


# ---------------------------------------------------------------------------
# `dotagents leak-check`: dispatches to tools/leak_check.py, returns its rc.
# ---------------------------------------------------------------------------

def test_leak_check_matches_standalone(tmp_path):
    # Scan a fresh, clean git repo (no .agents/ refs, no plan basenames, no
    # session trailers) so the tool has a deterministic PASS to match against.
    # Note: leak_check.py is meant to scan *consumer* repos; run against THIS
    # repo it reports the intentionally-tracked .agents/ references (not clean).
    subprocess.run(["git", "-C", str(tmp_path), "init", "-q"], check=True)
    (tmp_path / "README.md").write_text("hello world\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "-q", "-m", "init"],
        check=True,
    )

    standalone_rc = _standalone("leak_check.py", str(tmp_path))
    assert standalone_rc == 0  # a clean repo PASSES

    cmd = LeakCheck()
    cmd.repo = tmp_path
    assert cmd() == standalone_rc


def test_commits_only_skips_tracked_file_hits():
    """--commits-only skips the tracked-file scan. THIS repo intentionally tracks
    .agents/-referencing content (CI/tests/docs), so a full scan FAILS while a
    commit-message-only scan PASSES (its own history carries no session trailers).
    That gap is exactly what the flag exists to bridge."""
    full = subprocess.run(
        [sys.executable, str(REPO / "tools" / "leak_check.py"), str(REPO)],
        capture_output=True, text=True,
    )
    assert full.returncode == 1  # tracked-file hits fail the full scan here
    assert ".agents/" in full.stdout  # ... driven by tracked-file .agents/ refs

    commits = subprocess.run(
        [sys.executable, str(REPO / "tools" / "leak_check.py"),
         "--commits-only", str(REPO)],
        capture_output=True, text=True,
    )
    assert commits.returncode == 0  # commit messages are clean
    # The tracked-file hits are absent from the commits-only output.
    assert ".agents/" not in commits.stdout
    assert commits.stdout.strip().endswith("PASS")


def test_leak_check_commits_only_flag_forwarded():
    """The subcommand forwards --commits-only, matching the standalone rc."""
    standalone_rc = _standalone("leak_check.py", "--commits-only", str(REPO))
    cmd = LeakCheck()
    cmd.repo = REPO
    cmd.commits_only = True
    assert cmd() == standalone_rc == 0
