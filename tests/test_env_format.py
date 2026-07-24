"""Output-format + calling-shell detection tests for `dotagents env` (D83).

Pins the exact rendering (and especially the quoting/escaping) of every
`_format_env` format, the alias normalization, and that `detect_shell_format()`
always returns a known format and never raises. No network, no real ~/.agents --
these tests only exercise pure rendering + a process-name walk.

Run from repo root: ``python -m pytest tests/``.
"""

import json
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from dotagents import _env  # noqa: E402
from dotagents.cli.env import _format_env  # noqa: E402


# A sample env covering the tricky cases: a plain value, a value with a space, a
# value with a quote char, a value with a single quote, and an empty value.
SAMPLE = {
    "PLAIN": "abc",
    "SPACED": "a b",
    "DQUOTE": 'a"b',
    "SQUOTE": "a'b",
    "EMPTY": "",
}
# sorted key order (what _format_env emits): DQUOTE, EMPTY, PLAIN, SPACED, SQUOTE


def test_export_exact():
    assert _format_env(SAMPLE, "export") == "\n".join([
        'export DQUOTE="a\\"b"',
        'export EMPTY=""',
        'export PLAIN="abc"',
        'export SPACED="a b"',
        'export SQUOTE="a\'b"',
    ])


def test_dotenv_exact():
    # bare unless whitespace/#/"/newline; empty -> bare empty; DQUOTE + SPACED quoted.
    assert _format_env(SAMPLE, "dotenv") == "\n".join([
        'DQUOTE="a\\"b"',
        "EMPTY=",
        "PLAIN=abc",
        'SPACED="a b"',
        "SQUOTE=a'b",
    ])


def test_powershell_exact():
    # single-quoted; ' escaped as ''.
    assert _format_env(SAMPLE, "powershell") == "\n".join([
        "$env:DQUOTE = 'a\"b'",
        "$env:EMPTY = ''",
        "$env:PLAIN = 'abc'",
        "$env:SPACED = 'a b'",
        "$env:SQUOTE = 'a''b'",
    ])


def test_cmd_exact():
    # set "K=v"; embedded " emitted best-effort as "" (documented limitation).
    assert _format_env(SAMPLE, "cmd") == "\n".join([
        'set "DQUOTE=a""b"',
        'set "EMPTY="',
        'set "PLAIN=abc"',
        'set "SPACED=a b"',
        'set "SQUOTE=a\'b"',
    ])


def test_fish_exact():
    # single-quoted; ' escaped as \'.
    assert _format_env(SAMPLE, "fish") == "\n".join([
        "set -gx DQUOTE 'a\"b'",
        "set -gx EMPTY ''",
        "set -gx PLAIN 'abc'",
        "set -gx SPACED 'a b'",
        "set -gx SQUOTE 'a\\'b'",
    ])


def test_json_exact():
    import json

    out = _format_env(SAMPLE, "json")
    assert json.loads(out) == SAMPLE
    # sorted keys, indent=2
    assert out.startswith("{\n") and '"DQUOTE"' in out


def test_dotenv_hash_and_newline_quoted():
    env = {"H": "a#b", "N": "a\nb", "T": "a\tb"}
    out = _format_env(env, "dotenv")
    assert out == "\n".join([
        'H="a#b"',
        'N="a\\nb"',
        'T="a\tb"',
    ])


def test_powershell_multiple_single_quotes():
    # 3 inner quotes each doubled = 6, plus the 2 wrapping quotes = 8 total.
    assert _format_env({"K": "'''"}, "powershell") == "$env:K = ''''''''"


@pytest.mark.parametrize("alias,canonical", [
    ("posix", "export"),
    ("sh", "export"),
    ("bash", "export"),
    ("pwsh", "powershell"),
    ("ps", "powershell"),
    ("bat", "cmd"),
    ("batch", "cmd"),
    ("env", "dotenv"),
])
def test_alias_normalization(alias, canonical):
    assert _format_env(SAMPLE, alias) == _format_env(SAMPLE, canonical)


def test_alias_map_covers_known_formats():
    # auto is CLI-only (resolved before rendering), not a renderer alias.
    assert set(_env.KNOWN_FORMATS) == set(_env.FORMAT_ALIASES) | {"auto"}
    assert "auto" not in _env.FORMAT_ALIASES


def test_detect_shell_format_returns_known_and_never_raises():
    fmt = _env.detect_shell_format()  # must not raise on this platform
    assert fmt in set(_env.FORMAT_ALIASES.values())
    assert fmt != "auto"


def test_detect_shell_format_win_default(monkeypatch):
    # An empty process map -> Windows default "powershell", never raises.
    monkeypatch.setattr(_env, "_win_ppid_exe_map", lambda: {})
    assert _env._detect_shell_format_win() == "powershell"


def test_detect_shell_format_win_walks_chain(monkeypatch):
    import os

    me = os.getpid()
    # me -> python (not a shell) -> bash -> claude; first shell (bash) wins.
    fake = {
        me: (100, "python"),
        100: (200, "bash"),
        200: (300, "claude"),
        300: (0, "explorer"),
    }
    monkeypatch.setattr(_env, "_win_ppid_exe_map", lambda: fake)
    assert _env._detect_shell_format_win() == "export"


def test_detect_shell_format_win_finds_powershell(monkeypatch):
    import os

    me = os.getpid()
    fake = {me: (100, "python"), 100: (200, "pwsh"), 200: (0, "explorer")}
    monkeypatch.setattr(_env, "_win_ppid_exe_map", lambda: fake)
    assert _env._detect_shell_format_win() == "powershell"


def test_detect_shell_format_win_snapshot_failure(monkeypatch):
    # A raising snapshot must degrade to the default, not propagate.
    def boom():
        raise RuntimeError("snapshot failed")

    monkeypatch.setattr(_env, "_win_ppid_exe_map", boom)
    assert _env._detect_shell_format_win() == "powershell"


def test_detect_shell_format_posix_maps_parent(monkeypatch):
    # A fish parent -> "fish"; unknown -> "export". Never raises.
    monkeypatch.setattr(_env, "_posix_parent_comm", lambda ppid: "fish")
    assert _env._detect_shell_format_posix() == "fish"
    monkeypatch.setattr(_env, "_posix_parent_comm", lambda ppid: "zsh")
    assert _env._detect_shell_format_posix() == "export"
    monkeypatch.setattr(_env, "_posix_parent_comm", lambda ppid: "somethingelse")
    assert _env._detect_shell_format_posix() == "export"


def test_detect_shell_format_posix_graceful_on_failure(monkeypatch):
    def boom(ppid):
        raise RuntimeError("no parent")

    monkeypatch.setattr(_env, "_posix_parent_comm", boom)
    assert _env._detect_shell_format_posix() == "export"


def test_posix_parent_comm_falls_through_to_shell(monkeypatch, tmp_path):
    # Force /proc + ps to fail so $SHELL is used (macOS/OpenBSD-style fallback).
    import builtins

    real_open = builtins.open

    def no_proc(path, *a, **k):
        if str(path).startswith("/proc/"):
            raise OSError("no /proc here")
        return real_open(path, *a, **k)

    monkeypatch.setattr(builtins, "open", no_proc)

    import subprocess as sp

    def no_ps(*a, **k):
        raise OSError("no ps")

    monkeypatch.setattr(sp, "run", no_ps)
    monkeypatch.setenv("SHELL", "/usr/bin/fish")
    assert _env._posix_parent_comm(1) == "fish"


def test_norm_comm():
    assert _env._norm_comm("/usr/bin/bash") == "bash"
    assert _env._norm_comm("PowerShell.exe") == "powershell"
    assert _env._norm_comm("  zsh \n") == "zsh"


def test_auto_resolves_to_concrete_format():
    # `auto` must never reach the renderer; the CLI resolves it first.
    resolved = _env.detect_shell_format()
    assert resolved in _env.FORMAT_ALIASES
    # And the resolved format renders.
    assert isinstance(_format_env(SAMPLE, resolved), str)


# --------------------------------------------------------------------------- #
# PATH conversion for POSIX target formats (export/dotenv/fish).
#
# `get_environment` assembles PATH using the HOST OS's own convention -- on
# Windows, `os.pathsep` (`;`) and backslash `Path` separators, because that is
# what a Windows subprocess needs. A POSIX shell-sourceable format needs `:`
# and `/`, plus MSYS2/Cygwin's drive-letter-to-mount-point form (`C:/...` ->
# `/c/...`) for PATH LOOKUPS specifically -- slash direction alone is not
# enough; verified live that `command -v grep` resolves through `/c/Program
# Files/Git/usr/bin` but not `C:/Program Files/Git/usr/bin`, the same
# directory. Left unconverted, this is exactly what the Claude SessionStart
# hook writes into $CLAUDE_ENV_FILE -- sourcing it breaks PATH-based command
# lookup (git, grep, python, ...) for the rest of that session.
# --------------------------------------------------------------------------- #

WINDOWS_PATH = {
    "PATH": r"C:\Users\jose\.agents\bin;C:\Program Files\Git\usr\bin;.agents\bin",
    "PATHEXT": ".COM;.EXE;.BAT;.CMD",
    "AGENTS_HOME": r"C:\Users\jose\.agents",  # single path, NOT a list -- untouched
}


def test_export_converts_windows_path_to_posix():
    out = _format_env(WINDOWS_PATH, "export")
    assert (
        'export PATH="/c/Users/jose/.agents/bin:/c/Program Files/Git/usr/bin:'
        '.agents/bin"' in out
    )


def test_unc_path_keeps_double_slash_root():
    """`\\\\server\\share` -> `//server/share`, NOT `/server/share`.

    Both `Path.as_posix()` and a bare backslash-to-slash replace collapse a
    UNC path's leading `\\\\` to a single `/`, which MSYS does not accept as
    the UNC root -- caught by independent verification of this fix, which
    found the collapsed form was untested.
    """
    from dotagents.cli.env import _to_posix_path

    assert _to_posix_path("\\\\server\\share\\bin") == "//server/share/bin"


def test_export_drops_pathext():
    """PATHEXT has no POSIX meaning; dropped rather than emitted as garbage."""
    out = _format_env(WINDOWS_PATH, "export")
    assert "PATHEXT" not in out


def test_export_leaves_single_path_values_untouched():
    """Only PATH-LIST vars (name ends in PATH, value looks OS-native) convert --
    a plain single-path value must not be mangled."""
    out = _format_env(WINDOWS_PATH, "export")
    # export JSON-quotes values, so a literal backslash is doubled on the wire;
    # this is exactly what json.dumps(WINDOWS_PATH["AGENTS_HOME"]) produces.
    assert json.dumps(WINDOWS_PATH["AGENTS_HOME"]) in out


def test_dotenv_and_fish_also_convert_path():
    for fmt in ("dotenv", "fish"):
        out = _format_env(WINDOWS_PATH, fmt)
        assert "/c/Program Files/Git/usr/bin" in out
        path_line = next(l for l in out.split("\n") if l.startswith(("PATH=", "set -gx PATH")))
        assert "\\" not in path_line


def test_powershell_and_cmd_keep_native_path():
    """Non-POSIX formats must NOT be touched by the conversion -- Windows
    subprocesses need the native form, PATHEXT included."""
    for fmt in ("powershell", "cmd"):
        out = _format_env(WINDOWS_PATH, fmt)
        assert r"C:\Users\jose\.agents\bin" in out
        assert "PATHEXT" in out


def test_json_and_yaml_keep_native_path():
    """Data formats are for machine consumption of the RAW assembled env, not
    for sourcing -- must not silently rewrite values."""
    import json

    out = json.loads(_format_env(WINDOWS_PATH, "json"))
    assert out["PATH"] == WINDOWS_PATH["PATH"]
    assert "PATHEXT" in out


ILLEGAL_NAME_ENV = {
    "PATH": "/usr/bin",
    "ProgramFiles(x86)": r"C:\Program Files (x86)",
    "CommonProgramFiles(Arm)": r"C:\Program Files (Arm)\Common Files",
    "NORMAL_VAR": "ok",
}


def test_export_drops_posix_illegal_var_names():
    """`export FOO(X86)=...` is a bash SYNTAX ERROR, not a bad value -- it
    aborts the rest of the sourced file. A handful of real Windows env vars
    (ProgramFiles(x86), inherited via os.environ into base_env) have this
    shape; they must never reach a POSIX-targeted format."""
    out = _format_env(ILLEGAL_NAME_ENV, "export")
    assert "(" not in out
    assert ")" not in out
    assert "NORMAL_VAR" in out


def test_dotenv_and_fish_also_drop_illegal_names():
    for fmt in ("dotenv", "fish"):
        out = _format_env(ILLEGAL_NAME_ENV, fmt)
        assert "ProgramFiles" not in out
        assert "NORMAL_VAR" in out


def test_powershell_and_cmd_keep_illegal_named_vars():
    """Non-POSIX formats have no such restriction -- must not lose data."""
    for fmt in ("powershell", "cmd"):
        out = _format_env(ILLEGAL_NAME_ENV, fmt)
        assert "ProgramFiles(x86)" in out


def test_export_output_actually_sources_in_real_bash(tmp_path):
    """The end-to-end property that matters: the rendered output must be
    syntactically valid POSIX shell, sourceable with no error."""
    import subprocess

    script = tmp_path / "env.sh"
    script.write_text(_format_env(ILLEGAL_NAME_ENV, "export") + "\n", encoding="utf-8")
    proc = subprocess.run(
        ["sh", "-c", ". %s && echo OK" % json.dumps(str(script))],
        capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert "OK" in proc.stdout
