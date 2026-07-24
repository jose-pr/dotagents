"""Output-format + calling-shell detection tests for `dotagents env` (D83).

Pins the exact rendering (and especially the quoting/escaping) of every
`_format_env` format, the alias normalization, and that `detect_shell_format()`
always returns a known format and never raises. No network, no real ~/.agents --
these tests only exercise pure rendering + a process-name walk.

Run from repo root: ``python -m pytest tests/``.
"""

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
