"""Env-assembly regressions from the 2026-09-23 review, each a repro turned
into a test: malformed env.py output, relative PATH entries, a session whose
project root is the home directory, and the PowerShell rendering of
typographic quotes and non-ASCII values through the real loader pipe.

A case the product still gets wrong is a strict xfail naming the review
issue, so fixing it turns the test into an XPASS failure until the marker
goes. tmp dirs only, no network.
"""

import logging
import os
import shutil
import subprocess
import sys

import pytest

from dotagents import _env
from dotagents._scope import Scope


def _open(issue, why, condition=True):
    return pytest.mark.xfail(
        condition, strict=True, reason="open (review 2026-09-23 %s): %s" % (issue, why)
    )


@pytest.fixture
def roots(tmp_path):
    agents_dir = tmp_path / "agents"
    project_root = tmp_path / "proj"
    (project_root / ".agents").mkdir(parents=True)
    agents_dir.mkdir()
    return agents_dir, project_root


def _run(agents_dir, project_root, base_env=None, **kw):
    return _env.get_environment(
        Scope.of(agents_dir=agents_dir, project_root=project_root, global_scope=False),
        base_env=dict(base_env or {"PATH": os.pathsep.join([sys.prefix])}), **kw,
    )


# --------------------------------------------------------------------------
# A failing or malformed env.py contributes nothing and aborts nothing.
# --------------------------------------------------------------------------

_MALFORMED = {
    # bytes no locale codec on the runners decodes (0x81 is undefined in
    # cp1252 and invalid UTF-8)
    "undecodable-stdout": "import sys\nsys.stdout.buffer.write(b'{\"BAD\": \"\\x81\"}\\n')\n",
    "key-with-equals": "import json\nprint(json.dumps({'BAD=X': '1'}))\n",
    "nul-in-value": "import json\nprint(json.dumps({'BAD': 'a\\u0000b'}))\n",
}


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
@pytest.mark.parametrize("shape", sorted(_MALFORMED))
def test_a_malformed_env_py_does_not_abort_the_assembly(roots, shape):
    agents_dir, project_root = roots
    (agents_dir / "env.py").write_text(_MALFORMED[shape], encoding="utf-8")
    # A later file in the chain: its spawn is where a bad key/value crashed.
    (project_root / ".agents" / "env.py").write_text(
        "import json\nprint(json.dumps({'AFTER': '1'}))\n", encoding="utf-8"
    )
    env = _run(agents_dir, project_root, logger=logging.getLogger("t"))
    assert env.get("AFTER") == "1"
    assert not [k for k in env if k.startswith("BAD")]


# --------------------------------------------------------------------------
# PATH: no relative or empty entries survive into the assembled PATH.
# --------------------------------------------------------------------------

def test_relative_and_empty_path_entries_are_dropped(roots, tmp_path):
    agents_dir, project_root = roots
    absolute = str(tmp_path / "abs-bin")
    base = {"PATH": os.pathsep.join([absolute, "", "relative/bin", "."])}
    parts = _run(agents_dir, project_root, base)["PATH"].split(os.pathsep)
    assert absolute in parts
    assert all(_env._anchored(p) for p in parts), parts


def test_managed_bins_lead_path_in_contract_a_order_whatever_the_caller_had(roots, tmp_path):
    """A caller PATH that already holds the user bin (the hook prefix used to
    put it there) left it where it was, behind the system and overlay bins."""
    agents_dir, project_root = roots
    overlay = agents_dir / "overlays" / "x"  # a bin that must sort AFTER the user bin
    overlay.mkdir(parents=True)
    (overlay / "overlay.toml").write_text('name = "x"\n', encoding="utf-8")
    user_bin = str(agents_dir / "bin")
    other = str(tmp_path / "other-bin")
    base = {"PATH": os.pathsep.join([user_bin, other])}
    parts = _run(agents_dir, project_root, base)["PATH"].split(os.pathsep)
    managed = [str(p) for p in _env.get_bin_paths(
        Scope.of(agents_dir=agents_dir, project_root=project_root, global_scope=False))]
    assert parts[:len(managed)] == list(reversed(managed))
    assert parts.count(user_bin) == 1 and parts[-1] == other
    # Idempotent: applying the result again changes nothing.
    again = _run(agents_dir, project_root, {"PATH": os.pathsep.join(parts)})
    assert "PATH" not in again or again["PATH"] == os.pathsep.join(parts)


# --------------------------------------------------------------------------
# A session at the home directory: the project store IS the user store.
# --------------------------------------------------------------------------

def test_home_as_project_walks_the_user_store_once(tmp_path):
    home = tmp_path / "home"
    store = home / ".agents"
    store.mkdir(parents=True)
    counter = tmp_path / "runs.txt"
    (store / "env.py").write_text(
        "import json\nopen(%r, 'a').write('x')\nprint(json.dumps({}))\n" % str(counter),
        encoding="utf-8",
    )
    scope = Scope.of(agents_dir=store, project_root=home, global_scope=False)
    assert len(scope.stores) == len({p.resolve() for p in scope.stores})
    _env.get_environment(scope, base_env={"PATH": sys.prefix})
    assert counter.read_text(encoding="utf-8") == "x"


# --------------------------------------------------------------------------
# PowerShell: what the loader actually executes.
# --------------------------------------------------------------------------

_PWSH = shutil.which("pwsh") or shutil.which("powershell")
needs_powershell = pytest.mark.skipif(_PWSH is None, reason="needs PowerShell")


@needs_powershell
@pytest.mark.parametrize("value", [
    "C:\\Users\\Bob\u2019s project",
    "x\u2019; Write-Output 'INJECTED'; \u2019",
])
def test_powershell_output_survives_typographic_quotes(tmp_path, value):
    from dotagents.cli.env import _format_env

    script = tmp_path / "env.ps1"
    out = tmp_path / "out.txt"
    script.write_text(
        _format_env({"K": value}, "powershell") + "\n"
        + "[IO.File]::WriteAllText('%s', $env:K, [Text.UTF8Encoding]::new($false))\n" % out,
        encoding="utf-8-sig",
    )
    proc = subprocess.run([_PWSH, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
                          capture_output=True, text=True)
    assert "INJECTED" not in proc.stdout
    assert proc.returncode == 0, proc.stderr
    assert out.read_text(encoding="utf-8") == value


@pytest.mark.skipif(os.name != "nt", reason="the Windows PreToolUse loader")
@needs_powershell
def test_non_ascii_survives_the_real_loader_pipe(tmp_path, monkeypatch):
    """The PreToolUse loader pipes `dotagents.cmd env --diff --format
    powershell` into Invoke-Expression; a stub `dotagents.cmd` in the store
    emits a non-ASCII value exactly the way `env` writes it."""
    from dotagents._agents import ClaudeAgent

    store = tmp_path / "store"
    (store / "bin").mkdir(parents=True)
    emit = tmp_path / "emit.py"
    emit.write_text(
        "from dotagents.cli._common import _write_stdout\n"
        "from dotagents.cli.env import _format_env\n"
        "_write_stdout(_format_env({'K': 'Jos\\u00e9'}, 'powershell') + '\\n')\n",
        encoding="utf-8",
    )
    (store / "bin" / "dotagents.cmd").write_bytes(
        ('@echo off\r\n"%s" "%s" %%*\r\n' % (sys.executable, emit)).encode("utf-8")
    )
    monkeypatch.setenv("AGENTS_HOME", str(store))
    loader = ClaudeAgent.PRETOOLUSE_POWERSHELL_COMMAND.split("$p = '", 1)[1].split("'; ", 1)[0]
    out = tmp_path / "out.txt"
    code = (
        "[Console]::OutputEncoding = [Text.Encoding]::GetEncoding(437); "
        + loader
        + " [IO.File]::WriteAllText('%s', $env:K, [Text.UTF8Encoding]::new($false))" % out
    )
    proc = subprocess.run([_PWSH, "-NoProfile", "-Command", code], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert out.read_text(encoding="utf-8") == "Jos\u00e9"
