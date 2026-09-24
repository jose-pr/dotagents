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


# --------------------------------------------------------------------------
# env-04: a malformed env.py contributes nothing and never crashes assembly.
# --------------------------------------------------------------------------

def _two_layers(agents_dir, project_root, first_body):
    """A broken user-store env.py, then a good project one after it."""
    (agents_dir / "env.py").write_text(first_body, encoding="utf-8")
    (project_root / ".agents" / "env.py").write_text(
        "import json\nprint(json.dumps({'AFTER': 'ran'}))\n", encoding="utf-8"
    )


def test_undecodable_env_py_stdout_does_not_crash(roots):
    """stdout was decoded strictly in the locale codec: the reader thread died,
    `proc.stdout` stayed None and `.strip()` raised AttributeError."""
    agents_dir, project_root = roots
    _two_layers(agents_dir, project_root, (
        "import sys\n"
        "sys.stdout.buffer.write(b'{\"OK\": \"1\"}\\n{\"RAW\": \"\\x81\\xff\"}\\n')\n"
    ))
    env = _run(agents_dir, project_root)
    assert env["OK"] == "1"
    assert env["AFTER"] == "ran"


def test_env_py_non_ascii_output_is_read_as_utf8(roots):
    """The child wrote with the console code page (cp1252 on Windows), so an
    emoji in a value killed it with UnicodeEncodeError."""
    agents_dir, project_root = roots
    (agents_dir / "env.py").write_text(
        "import json\nprint(json.dumps({'U': 'caf\\u00e9 \\U0001F600'}, ensure_ascii=False))\n",
        encoding="utf-8",
    )
    env = _run(agents_dir, project_root)
    assert env["U"] == "café \U0001F600"


@pytest.mark.parametrize("payload,bad", [
    ({"A=B": "x", "OK": "1"}, "A=B"),
    ({"": "x", "OK": "1"}, ""),
    ({"N": "a\u0000b", "OK": "1"}, "N"),
])
def test_invalid_names_and_nul_values_are_dropped(roots, payload, bad):
    """A key with `=` or a value with NUL was merged, then crashed the NEXT
    layer's spawn (`illegal environment variable name` / `embedded null
    character`), taking the whole env down."""
    agents_dir, project_root = roots
    _two_layers(agents_dir, project_root, "import json\nprint(json.dumps(%r))\n" % (payload,))
    env = _run(agents_dir, project_root)
    assert env["OK"] == "1"
    assert bad not in env
    assert env["AFTER"] == "ran"


def test_a_layer_that_raises_is_skipped(roots, monkeypatch, caplog):
    """Any exception in one layer's evaluation is contained to that layer and
    logged by file name only."""
    import logging

    agents_dir, project_root = roots
    _two_layers(agents_dir, project_root, "print('{}')\n")
    real = _env.get_env_from_py

    def boom(path, *a, **kw):
        if path.parent == agents_dir:
            raise RuntimeError("secret-value-must-not-be-logged")
        return real(path, *a, **kw)

    monkeypatch.setattr(_env, "get_env_from_py", boom)
    with caplog.at_level(logging.WARNING, logger="t"):
        env = _run(agents_dir, project_root, logger=logging.getLogger("t"))
    assert env["AFTER"] == "ran"
    assert not any("secret-value" in r.getMessage() for r in caplog.records)
    assert any(str(agents_dir / "env.py") in r.getMessage() for r in caplog.records)
