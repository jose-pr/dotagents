"""Regression tests for the env assembly and rendering review fixes
(review 2026-09-23, "Env assembly and rendering").

tmp dirs only, no network. Run from repo root: ``python -m pytest tests/``.
"""

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dotagents import _env  # noqa: E402
from dotagents._scope import Scope  # noqa: E402


@pytest.fixture
def roots(tmp_path):
    agents_dir = tmp_path / "agents"
    project_root = tmp_path / "proj"
    (project_root / ".agents").mkdir(parents=True)
    agents_dir.mkdir()
    return agents_dir, project_root


def _scope(agents_dir, project_root, global_scope=False):
    return Scope.of(agents_dir=agents_dir, project_root=project_root, global_scope=global_scope)


def _run(agents_dir, project_root, base=None, **kw):
    return _env.get_environment(
        _scope(agents_dir, project_root),
        base_env=dict(base if base is not None else {"PATH": "/usr/bin"}),
        **kw,
    )


# --------------------------------------------------------------------------
# env-15: the env.py argv contract, and a login shell's leading dash.
# --------------------------------------------------------------------------

def test_env_py_is_told_its_level_as_level(roots):
    """`env.py` was run as `--agent <level>`: the value is a contract-A level
    (or an overlay name), never an agent. `--level` names it; `--agent` stays
    for scripts that already read it."""
    agents_dir, project_root = roots
    (agents_dir / "env.py").write_text(
        "import json, sys\nprint(json.dumps({'ARGV': ' '.join(sys.argv[1:])}))\n",
        encoding="utf-8",
    )
    env = _run(agents_dir, project_root)
    argv = env["ARGV"].split()
    assert argv[argv.index("--level") + 1] == "user"
    assert argv[argv.index("--agent") + 1] == "user"


def test_norm_comm_strips_the_login_shell_dash():
    """macOS `ps -o comm=` reports a login shell as `-fish`: it fell through
    to `export`, handing a fish user bash syntax."""
    assert _env._norm_comm("-fish") == "fish"
    assert _env._norm_comm("-zsh") == "zsh"
