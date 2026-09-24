"""Regression tests for the env assembly and rendering review fixes
(review 2026-09-23, "Env assembly and rendering").

tmp dirs only, no network. Run from repo root: ``python -m pytest tests/``.
"""

import json
import os
import shutil
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


# --------------------------------------------------------------------------
# env-05: a real bash, never the WSL launcher.
# --------------------------------------------------------------------------

_WINDOWSAPPS = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WindowsApps"


@pytest.mark.skipif(os.name != "nt", reason="the WSL launcher is a Windows trap")
@pytest.mark.skipif(not (_WINDOWSAPPS / "bash.exe").is_file(), reason="no WindowsApps bash.exe")
@pytest.mark.skipif(shutil.which("git") is None, reason="needs Git for Windows")
def test_windowsapps_bash_ahead_of_git_is_not_used(tmp_path, monkeypatch):
    """`shutil.which('bash')` returned WindowsApps\\bash.exe whenever it came
    before Git on PATH (the usual PowerShell case); it sources the file inside
    WSL, where the Windows path does not exist, so every plain env file was
    dropped."""
    monkeypatch.setenv("PATH", os.pathsep.join([
        str(_WINDOWSAPPS),
        str(Path(shutil.which("git")).parent),
        os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32"),
    ]))
    monkeypatch.setattr(_env, "_BASH_RESOLVED", [], raising=False)
    env_file = tmp_path / "env"
    env_file.write_text("export ONLY=me\n", encoding="utf-8")
    changes = _env.get_env_from_file(env_file, base_env=dict(os.environ))
    assert changes.get("ONLY") == "me"
    bash = _env.find_bash()
    assert os.path.normcase(str(_WINDOWSAPPS)) not in os.path.normcase(bash)


_BASH = getattr(_env, "find_bash", lambda: shutil.which("bash"))()
needs_bash = pytest.mark.skipif(_BASH is None, reason="needs a working bash")
windows_only = pytest.mark.skipif(os.name != "nt", reason="MSYS2 env rewriting is Windows-only")


# --------------------------------------------------------------------------
# env-01: what MSYS2 bash rewrites at startup is not the file's change.
# --------------------------------------------------------------------------

@needs_bash
@windows_only
def test_plain_env_file_change_set_is_exactly_what_it_exports(tmp_path):
    """Against the REAL environment, Git Bash's startup rewriting (PATH, HOME,
    TEMP, TMP, SHELL into /c/... form; PROCESSOR_ARCHITECTURE under an x64
    bash on ARM64) came back as changes the file made."""
    env_file = tmp_path / "env"
    env_file.write_text("export ONLY=me\n", encoding="utf-8")
    changes = _env.get_env_from_file(env_file, base_env=dict(os.environ))
    assert dict(changes) == {"ONLY": "me"}


@needs_bash
@windows_only
def test_a_path_the_file_extends_comes_back_in_windows_form(tmp_path):
    """The file's own addition is converted with cygpath and spliced onto the
    caller's PATH verbatim -- no POSIX segments, no Git launcher dirs."""
    base = dict(os.environ)
    env_file = tmp_path / "env"
    env_file.write_text('export PATH="/c/dotagents-probe:$PATH"\n', encoding="utf-8")
    changes = _env.get_env_from_file(env_file, base_env=base)
    assert set(changes) == {"PATH"}
    assert changes["PATH"] == "C:\\dotagents-probe;" + base["PATH"]


@needs_bash
@windows_only
def test_an_env_py_after_a_plain_file_sees_the_native_environment(roots):
    """The chain poisoning: an env.py after a plain file got TEMP=/tmp and a
    POSIX PATH, so it could not even find git."""
    agents_dir, project_root = roots
    (agents_dir / "env").write_text("export ONLY=me\n", encoding="utf-8")
    (project_root / ".agents" / "env.py").write_text(
        "import json, os, shutil\n"
        "print(json.dumps({'SEEN_TEMP': os.environ.get('TEMP', ''),"
        " 'SEEN_GIT': shutil.which('git') or ''}))\n",
        encoding="utf-8",
    )
    base = dict(os.environ)
    env = _run(agents_dir, project_root, base)
    assert env["ONLY"] == "me"
    assert env["SEEN_TEMP"] == base.get("TEMP", "")
    if shutil.which("git"):
        assert env["SEEN_GIT"]
    for rewritten in ("HOME", "TEMP", "TMP", "SHELL", "PROCESSOR_ARCHITECTURE"):
        assert rewritten not in env


@needs_bash
def test_a_file_that_exits_early_is_a_failed_source(tmp_path, caplog):
    """`exit 0` inside the file ended bash before `env -0` ran: an empty
    stdout with rc 0 looked like a file that changed nothing."""
    import logging

    env_file = tmp_path / "env"
    env_file.write_text("export BEFORE=1\nexit 0\n", encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="t"):
        changes = _env.get_env_from_file(
            env_file, base_env=dict(os.environ), logger=logging.getLogger("t")
        )
    assert "BEFORE" not in changes
    assert any("source failed" in r.getMessage() for r in caplog.records)


# --------------------------------------------------------------------------
# env-14: env.py values are strings (null = unset); plain files can unset.
# --------------------------------------------------------------------------

def test_env_py_values_must_be_strings_and_null_unsets(roots, caplog):
    """`{k: str(v)}` turned null into 'None', false into 'False' and an array
    into its Python repr, and nothing could unset a variable."""
    import logging

    agents_dir, project_root = roots
    (agents_dir / "env.py").write_text(
        "import json\nprint(json.dumps({'CLEAR_ME': None, 'FLAG': False,"
        " 'ARR': ['a', 'b'], 'NUM': 1, 'S': 'ok'}))\n",
        encoding="utf-8",
    )
    base = {"PATH": "/usr/bin", "CLEAR_ME": "old"}
    with caplog.at_level(logging.WARNING, logger="t"):
        env = _run(agents_dir, project_root, base, logger=logging.getLogger("t"))
    assert env["S"] == "ok"
    for bad in ("FLAG", "ARR", "NUM", "CLEAR_ME"):
        assert bad not in env
    assert env.removed == {"CLEAR_ME"}
    warned = " ".join(r.getMessage() for r in caplog.records)
    assert "FLAG" in warned and "ARR" in warned and "NUM" in warned


def test_a_later_layer_can_set_what_an_earlier_one_unset(roots):
    agents_dir, project_root = roots
    (agents_dir / "env.py").write_text(
        "import json\nprint(json.dumps({'X': None, 'Y': None}))\n", encoding="utf-8"
    )
    (project_root / ".agents" / "env.py").write_text(
        "import json, os\nprint(json.dumps({'X': 'back', 'SAW_Y': str('Y' in os.environ)}))\n",
        encoding="utf-8",
    )
    env = _run(agents_dir, project_root, {"PATH": "/usr/bin", "X": "1", "Y": "1"})
    assert env["X"] == "back"
    assert env["SAW_Y"] == "False"  # the unset reached the next layer's env
    assert env.removed == {"Y"}


def test_unsetting_a_var_the_base_never_had_is_no_change(roots):
    agents_dir, project_root = roots
    (agents_dir / "env.py").write_text(
        "import json\nprint(json.dumps({'NEVER_SET': None}))\n", encoding="utf-8"
    )
    diff = _env.get_diff(_scope(agents_dir, project_root), base_env={"PATH": "/usr/bin"})
    assert "NEVER_SET" not in diff and not diff.removed


@needs_bash
def test_plain_env_file_unset_is_reported(tmp_path):
    """`_changed_env` reported only added or changed keys, so `unset FOO` in a
    plain env file was dropped silently."""
    env_file = tmp_path / "env"
    env_file.write_text("unset KEEP_ME\nexport ADDED=1\n", encoding="utf-8")
    base = dict(os.environ, KEEP_ME="x")
    changes = _env.get_env_from_file(env_file, base_env=base)
    assert dict(changes) == {"ADDED": "1"}
    assert changes.removed == {"KEEP_ME"}


def test_removals_render_per_format():
    from dotagents.cli.env import _format_env

    env = _env.EnvChanges({"A": "1"}, removed={"B"})
    assert _format_env(env, "export") == "export A='1'\nunset B"
    assert _format_env(env, "fish") == "set -gx A '1'\nset -e B"
    assert _format_env(env, "powershell") == "${env:A} = '1'\n${env:B} = $null"
    assert _format_env(env, "cmd") == 'set "A=1"\nset "B="'
    assert json.loads(_format_env(env, "json")) == {"A": "1", "B": None}
    assert "B" not in _format_env(env, "dotenv")
    assert "B" not in _format_env(env, "ini")
    yaml = pytest.importorskip("yaml")
    assert yaml.safe_load(_format_env(env, "yaml")) == {"A": "1", "B": None}


@needs_bash
def test_export_unset_really_unsets_in_bash(tmp_path):
    import subprocess
    from dotagents.cli.env import _format_env

    script = tmp_path / "env.sh"
    script.write_text(
        _format_env(_env.EnvChanges({"A": "1"}, removed={"B"}), "export") + "\n",
        encoding="utf-8",
    )
    proc = subprocess.run(
        [_BASH, "-c", '. "$1"; printf "%s|%s" "$A" "${B-unset}"', "bash", str(script)],
        capture_output=True, env=dict(os.environ, B="was-set"),
    )
    assert proc.stdout.decode() == "1|unset"


def test_env_cli_emits_the_unset(roots, monkeypatch, capsys):
    from dotagents.cli.env import Env

    agents_dir, project_root = roots
    (agents_dir / "env.py").write_text(
        "import json\nprint(json.dumps({'GO_AWAY': None}))\n", encoding="utf-8"
    )
    monkeypatch.setenv("AGENTS_HOME", str(agents_dir))
    monkeypatch.setenv("AGENTS_PROJECT_ROOT", str(project_root))
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    monkeypatch.setenv("GO_AWAY", "1")
    for diff in (True, False):
        cmd = Env()
        cmd.format = "json"
        cmd.diff = diff
        assert cmd() == 0
        out = json.loads(capsys.readouterr().out)
        assert out["GO_AWAY"] is None


# --------------------------------------------------------------------------
# env-08: overlay lib/ dirs never reach the session's PYTHONPATH.
# --------------------------------------------------------------------------

def test_an_overlay_lib_does_not_shadow_the_stdlib_session_wide(roots):
    """Every existing lib/ was prepended to the session's PYTHONPATH, ahead of
    site-packages and the stdlib for every Python the agent runs: an overlay
    module shadowed any same-named one (the net overlay's certifi shim broke
    every `requests` HTTPS call)."""
    import subprocess

    agents_dir, project_root = roots
    lib = agents_dir / "overlays" / "shadow" / "lib"
    lib.mkdir(parents=True)
    (lib / "csv.py").write_text("SHADOW = True\n", encoding="utf-8")
    base = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env = _run(agents_dir, project_root, base)
    assert "PYTHONPATH" not in env
    assert env["AGENTS_PYTHONPATH"] == str(lib)
    session = dict(base)
    session.update(env)
    out = subprocess.run(
        [sys.executable, "-c", "import csv; print(hasattr(csv, 'SHADOW'))"],
        capture_output=True, text=True, env=session,
    ).stdout.strip()
    assert out == "False"


def test_env_py_child_gets_the_libs_ahead_of_the_inherited_pythonpath(roots):
    agents_dir, project_root = roots
    (agents_dir / "lib").mkdir()
    (project_root / ".agents" / "lib").mkdir()
    (agents_dir / "env.py").write_text(
        "import json, os\nprint(json.dumps({'SEEN': os.environ.get('PYTHONPATH', '')}))\n",
        encoding="utf-8",
    )
    extra = str(Path(os.sep) / "site" / "extra")
    env = _run(agents_dir, project_root, {"PATH": "/usr/bin", "PYTHONPATH": extra})
    assert env["SEEN"].split(os.pathsep) == [
        str(project_root / ".agents" / "lib"), str(agents_dir / "lib"), extra,
    ]
    assert "PYTHONPATH" not in env


def test_agents_pythonpath_from_another_scope_is_replaced(roots):
    agents_dir, project_root = roots
    stale = {"PATH": "/usr/bin", "AGENTS_PYTHONPATH": str(Path(os.sep) / "elsewhere" / "lib")}
    env = _run(agents_dir, project_root, stale)
    assert env.removed == {"AGENTS_PYTHONPATH"}
    (agents_dir / "lib").mkdir()
    env = _run(agents_dir, project_root, stale)
    assert env["AGENTS_PYTHONPATH"] == str(agents_dir / "lib")


# --------------------------------------------------------------------------
# env-10: the emitted roots are the stores the walk used, absolute.
# --------------------------------------------------------------------------

def test_emitted_agents_home_is_the_store_walked(tmp_path):
    """Inside a session pinned to store A, `env --agents-dir B` walked B but
    emitted AGENTS_HOME=A (only-if-unset), so later commands acted on A."""
    store_a, store_b = tmp_path / "storeA", tmp_path / "storeB"
    (store_b / "overlays" / "foo").mkdir(parents=True)
    store_a.mkdir()
    env = _env.get_environment(
        Scope.of(agents_dir=store_b, global_scope=True),
        base_env={"PATH": "/usr/bin", "AGENTS_HOME": str(store_a)},
    )
    assert env["AGENTS_HOME"] == str(store_b)
    assert env["FOO_OVERLAY_ROOT"] == str(store_b / "overlays" / "foo")


def test_emitted_project_root_is_the_project_walked(tmp_path):
    store = tmp_path / "store"
    proj_a, proj_b = tmp_path / "projA", tmp_path / "projB"
    for d in (store, proj_a / ".agents", proj_b / ".agents"):
        d.mkdir(parents=True)
    env = _env.get_environment(
        _scope(store, proj_b),
        base_env={"PATH": "/usr/bin", "AGENTS_PROJECT_ROOT": str(proj_a)},
    )
    assert env["AGENTS_PROJECT_ROOT"] == str(proj_b)


def test_an_inherited_root_naming_the_same_dir_holds(tmp_path):
    """No spurious diff when the pin is the same directory spelled
    differently (a trailing separator, and case on Windows)."""
    store, proj = tmp_path / "store", tmp_path / "proj"
    (proj / ".agents").mkdir(parents=True)
    store.mkdir()
    spelled = str(store) + os.sep
    if os.name == "nt":
        spelled = spelled.upper()
    base = {"PATH": "/usr/bin", "AGENTS_HOME": spelled, "AGENTS_PROJECT_ROOT": str(proj)}
    diff = _env.get_diff(_scope(store, proj), base_env=base)
    assert "AGENTS_HOME" not in diff and "AGENTS_PROJECT_ROOT" not in diff


def test_relative_roots_are_emitted_absolute(tmp_path, monkeypatch):
    """`env --agents-dir ./storeB --diff` emitted "AGENTS_HOME": "storeB",
    which resolves differently in a child running in another cwd."""
    (tmp_path / "storeB" / "overlays" / "foo" / "bin").mkdir(parents=True)
    (tmp_path / "proj" / ".agents").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    env = _env.get_environment(
        Scope.of(agents_dir=Path("storeB"), project_root=Path("proj")),
        base_env={"PATH": "/usr/bin"},
    )
    assert env["AGENTS_HOME"] == str(tmp_path / "storeB")
    assert env["AGENTS_PROJECT_ROOT"] == str(tmp_path / "proj")
    assert env["FOO_OVERLAY_ROOT"] == str(tmp_path / "storeB" / "overlays" / "foo")
    assert str(tmp_path / "storeB" / "overlays" / "foo" / "bin") in env["PATH"].split(os.pathsep)


def test_env_cli_agents_dir_wins_over_the_inherited_store(tmp_path, monkeypatch, capsys):
    from dotagents.cli.env import Env

    (tmp_path / "storeA").mkdir()
    (tmp_path / "storeB").mkdir()
    monkeypatch.setenv("AGENTS_HOME", str(tmp_path / "storeA"))
    monkeypatch.delenv("AGENTS_PROJECT_ROOT", raising=False)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    cmd = Env()
    cmd.format = "json"
    cmd.diff = True
    cmd.global_scope = True
    cmd.agents_dir = Path("storeB")
    assert cmd() == 0
    out = json.loads(capsys.readouterr().out)
    assert out["AGENTS_HOME"] == str(tmp_path / "storeB")


# --------------------------------------------------------------------------
# env-07: the export format is POSIX sh, not bash.
# --------------------------------------------------------------------------

def _posix_shells():
    shells = []
    if _BASH:
        shells.append(("bash", [_BASH]))
        shells.append(("bash --posix", [_BASH, "--posix"]))
    if os.name != "nt":
        for name in ("dash", "sh", "ksh"):
            found = shutil.which(name)
            if found:
                shells.append((name, [found]))
    return shells


_TRICKY = {
    "INJ": "\x01'; echo INJ\"\"ECTED; #",
    "NL": "line1\nline2\n",
    "CR": "a\rb\r",
    "TAB": "a\tb'c",
    "BS": "back\\slash\\'",
}


def _parse_posix_sh_word(word):
    """Decode a shell word built ONLY from what pre-2024 POSIX sh has: '...'
    segments, a backslash-escaped quote, and the constant CR splice. Raises on
    anything else -- in particular `$'...'` (a bash/ksh extension until POSIX
    2024; dash before 0.5.13 has none of it)."""
    import re

    token = re.compile(r"'([^']*)'|\\(')|\"\$\(printf '\\r'\)\"")
    out, pos = [], 0
    while pos < len(word):
        m = token.match(word, pos)
        if not m:
            raise ValueError("not POSIX single-quoting at %d: %r" % (pos, word[pos:pos + 12]))
        out.append(m.group(1) if m.group(1) is not None else (m.group(2) or "\r"))
        pos = m.end()
    return "".join(out)


def test_export_quoting_is_plain_posix_for_every_value():
    from dotagents.cli.env import _format_env

    for key, value in _TRICKY.items():
        line = _format_env({key: value}, "export")
        prefix = "export %s=" % key
        assert line.startswith(prefix)
        assert _parse_posix_sh_word(line[len(prefix):]) == value


@pytest.mark.parametrize("label,argv", _posix_shells() or [pytest.param("none", None, marks=pytest.mark.skip("no POSIX shell"))])
def test_export_values_roundtrip_through_every_posix_sh(tmp_path, label, argv):
    """A value with a control character switched to bash's `$'...'` form, with
    `'` written as `\\'`; dash has no `$'...'`, so there `\\'` ended the quote
    and `export K=$'\\x01\\'; id; #'` RAN `id`."""
    import subprocess
    from dotagents.cli.env import _format_env

    script = tmp_path / "env.sh"
    script.write_bytes((_format_env(_TRICKY, "export") + "\n").encode("utf-8"))
    probe = "import json, os; print(json.dumps({k: os.environ.get(k) for k in %r}))" % sorted(_TRICKY)
    proc = subprocess.run(
        argv + ["-c", '. "$1" && exec "$2" -c "$3"', "sh", str(script), sys.executable, probe],
        capture_output=True,
    )
    assert b"INJECTED" not in proc.stdout, label
    assert proc.returncode == 0, (label, proc.stderr)
    got = json.loads(proc.stdout.decode("utf-8").strip().splitlines()[-1])
    assert got == _TRICKY, label


def test_find_bash_warns_once_when_there_is_none(monkeypatch, caplog):
    import logging

    monkeypatch.setattr(_env, "_BASH_RESOLVED", [None])
    monkeypatch.setattr(_env, "_BASH_WARNED", [])
    log = logging.getLogger("t")
    with caplog.at_level(logging.WARNING, logger="t"):
        assert _env.find_bash(log) is None
        assert _env.find_bash(log) is None
    assert sum("no usable bash" in r.getMessage() for r in caplog.records) == 1
