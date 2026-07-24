"""Tests for command discovery (D76): `dotagents.cli._discover` resolving the
built-in commands plus command modules from the bundled cmds dir, the per-scope
cmds dirs (user + project), `$DOTAGENTS_CMDS_PATH`, and `--cmdspath`.

Filesystem-only (tmp_path); no network. NEVER exports HOME/USERPROFILE -- the
user scope is redirected via `$DOTAGENTS_AGENTS_DIR` and the project scope via
`monkeypatch.chdir`, so a real `~/.agents` is never touched.
"""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dotagents import cli  # noqa: E402


TOY = '''\
"""A toy discovered command for tests."""

from duho import Cmd, LoggingArgs


class Toy(LoggingArgs, Cmd):
    """A toy command."""

    _parsername_ = "toy"

    greeting: str = "hi"
    ("--greeting",)

    def __call__(self) -> int:
        print(self.greeting)
        return 0
'''

# A command module that shadows the discovered `link` command, to prove
# later-source-wins dedup by _parsername_.
SHADOW_LINK = '''\
"""A command module claiming the `link` name (shadow test)."""

from duho import Cmd, LoggingArgs


class ShadowLink(LoggingArgs, Cmd):
    """Shadow link."""

    _parsername_ = "link"

    def __call__(self) -> int:
        return 0
'''

BROKEN = "this is not valid python ((("


def _names(commands):
    out = []
    for c in commands:
        out.append(getattr(c, "_parsername_", None) or getattr(c, "__name__", None))
    return out


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


# --------------------------------------------------------------------------- #
# Baseline: built-ins + bundled link/sync
# --------------------------------------------------------------------------- #


def test_discover_includes_builtins_and_bundled_link_sync(monkeypatch, tmp_path):
    # Point both scopes at empty dirs so only built-ins + bundled cmds contribute.
    monkeypatch.setenv("DOTAGENTS_AGENTS_DIR", str(tmp_path / "user" / ".agents"))
    monkeypatch.delenv("DOTAGENTS_CMDS_PATH", raising=False)
    monkeypatch.chdir(tmp_path)

    names = _names(cli._discover([]))
    # Built-ins survive the app switch.
    for builtin in ("init", "install", "audit", "leak-check", "build-pyz",
                    "context", "env", "overlays"):
        assert builtin in names
    # link/sync come from the bundled cmds dir, not `_subcommands_`.
    assert "link" in names
    assert "sync" in names
    # They are no longer compiled built-ins.
    assert cli.Dotagents._subcommands_ == []


# --------------------------------------------------------------------------- #
# Env var + flag extra sources
# --------------------------------------------------------------------------- #


def test_discover_honors_cmds_path_env(monkeypatch, tmp_path):
    cmds = tmp_path / "extra"
    _write(cmds / "toy.py", TOY)
    monkeypatch.setenv("DOTAGENTS_AGENTS_DIR", str(tmp_path / "user" / ".agents"))
    monkeypatch.setenv("DOTAGENTS_CMDS_PATH", str(cmds))
    monkeypatch.chdir(tmp_path)

    names = _names(cli._discover([]))
    assert "toy" in names


def test_discover_honors_cmdspath_flag(monkeypatch, tmp_path):
    cmds = tmp_path / "flagcmds"
    _write(cmds / "toy.py", TOY)
    monkeypatch.setenv("DOTAGENTS_AGENTS_DIR", str(tmp_path / "user" / ".agents"))
    monkeypatch.delenv("DOTAGENTS_CMDS_PATH", raising=False)
    monkeypatch.chdir(tmp_path)

    names = _names(cli._discover(["--cmdspath", str(cmds)]))
    assert "toy" in names


# --------------------------------------------------------------------------- #
# Scope walk: user AND project both contribute
# --------------------------------------------------------------------------- #


def test_user_scope_contributes(monkeypatch, tmp_path):
    user_root = tmp_path / "user" / ".agents"
    _write(user_root / "dotagents" / "cmds" / "toy.py", TOY)
    monkeypatch.setenv("DOTAGENTS_AGENTS_DIR", str(user_root))
    monkeypatch.delenv("DOTAGENTS_CMDS_PATH", raising=False)
    monkeypatch.chdir(tmp_path)

    names = _names(cli._discover([]))
    assert "toy" in names


def test_project_scope_contributes(monkeypatch, tmp_path):
    proj = tmp_path / "proj"
    _write(proj / ".agents" / "dotagents" / "cmds" / "toy.py", TOY)
    monkeypatch.setenv("DOTAGENTS_AGENTS_DIR", str(tmp_path / "user" / ".agents"))
    monkeypatch.delenv("DOTAGENTS_CMDS_PATH", raising=False)
    monkeypatch.chdir(proj)

    names = _names(cli._discover([]))
    assert "toy" in names


# --------------------------------------------------------------------------- #
# Resilience + dedup
# --------------------------------------------------------------------------- #


def test_bad_source_is_skipped_not_fatal(monkeypatch, tmp_path):
    # A nonexistent dir + a syntactically-broken command file must not crash.
    good = tmp_path / "good"
    _write(good / "toy.py", TOY)
    missing = tmp_path / "does-not-exist"
    monkeypatch.setenv("DOTAGENTS_AGENTS_DIR", str(tmp_path / "user" / ".agents"))
    monkeypatch.setenv(
        "DOTAGENTS_CMDS_PATH", os.pathsep.join([str(missing), str(good)])
    )
    monkeypatch.chdir(tmp_path)

    # A missing dir is simply skipped; the good source still loads.
    names = _names(cli._discover([]))
    assert "toy" in names


def test_later_source_wins_dedup(monkeypatch, tmp_path):
    # The bundled cmds provide `link`; an env-var source shadows it. Later wins.
    shadow = tmp_path / "shadow"
    _write(shadow / "shadowlink.py", SHADOW_LINK)
    monkeypatch.setenv("DOTAGENTS_AGENTS_DIR", str(tmp_path / "user" / ".agents"))
    monkeypatch.setenv("DOTAGENTS_CMDS_PATH", str(shadow))
    monkeypatch.chdir(tmp_path)

    commands = cli._discover([])
    link_cmds = [
        c for c in commands
        if (getattr(c, "_parsername_", None) or getattr(c, "__name__", None)) == "link"
    ]
    # Exactly one `link` in the resolved set (dedup), and it is the shadow.
    assert len(link_cmds) == 1
    assert link_cmds[0].__name__ == "ShadowLink"


def test_project_overrides_user_scope(monkeypatch, tmp_path):
    # Same `toy` name in both scopes: project (later in the walk) wins.
    user_root = tmp_path / "user" / ".agents"
    _write(user_root / "dotagents" / "cmds" / "toy.py", TOY)
    proj = tmp_path / "proj"
    _write(
        proj / ".agents" / "dotagents" / "cmds" / "toy.py",
        TOY.replace('_parsername_ = "toy"', '_parsername_ = "toy"\n    marker = "project"'),
    )
    monkeypatch.setenv("DOTAGENTS_AGENTS_DIR", str(user_root))
    monkeypatch.delenv("DOTAGENTS_CMDS_PATH", raising=False)
    monkeypatch.chdir(proj)

    commands = cli._discover([])
    toy = [
        c for c in commands
        if (getattr(c, "_parsername_", None) or getattr(c, "__name__", None)) == "toy"
    ]
    assert len(toy) == 1
    assert getattr(toy[0], "marker", None) == "project"
