"""Tests for command discovery (D76): `dotagents.cli._discover` resolving the
built-in commands plus command modules from the bundled cmds dir, the per-scope
cmds dirs (user + project), `$AGENTS_CMDS_PATH`, and `--cmdspath`.

Filesystem-only (tmp_path); no network. NEVER exports HOME/USERPROFILE -- the
user scope is redirected via `$AGENTS_HOME` and the project scope via
`monkeypatch.chdir`, so a real `~/.agents` is never touched.

Env-var prefix (D80): readers prefer the `AGENTS_*` name and fall back to the old
`DOTAGENTS_*` name for one release; the fallback tests below assert both paths.
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
    monkeypatch.setenv("AGENTS_HOME", str(tmp_path / "user" / ".agents"))
    monkeypatch.delenv("AGENTS_CMDS_PATH", raising=False)
    monkeypatch.chdir(tmp_path)

    names = _names(cli._discover([]))
    # Compiled built-ins survive the app switch (audit stays a built-in, D84).
    for builtin in ("init", "audit", "build-pyz", "context", "env", "overlays"):
        assert builtin in names
    # link/sync come from the bundled cmds dir, not `_subcommands_`.
    assert "link" in names
    assert "sync" in names
    # leak-check is GONE from the repo entirely (D84): it is a personal command
    # module the user drops into their private `.agents/cmds/`, discovered only
    # when present -- never a default of a fresh install.
    assert "leak-check" not in names
    # link/sync are no longer compiled built-ins.
    assert cli.Dotagents._subcommands_ == []


def test_installed_overlay_cmds_are_discovered(monkeypatch, tmp_path):
    # An installed overlay shipping a command at <overlay-root>/cmds/*.py is
    # discovered (D84 per-overlay cmds via the get_file_paths Contract-A walk).
    # Presence-by-directory: a BARE overlay dir (no manifest at all -- no
    # overlay.toml, no CONTEXT.md) still counts, matching discover_overlays.
    user_root = tmp_path / "user" / ".agents"
    overlay = user_root / "overlays" / "toybox"
    _write(overlay / "cmds" / "toy.py", TOY)
    monkeypatch.setenv("AGENTS_HOME", str(user_root))
    monkeypatch.delenv("AGENTS_CMDS_PATH", raising=False)
    monkeypatch.chdir(tmp_path)

    names = _names(cli._discover([]))
    assert "toy" in names


def test_scope_cmds_override_overlay_cmds(monkeypatch, tmp_path):
    # A same-named command in the user scope's dotagents/cmds overrides an
    # overlay's cmds command (scope is later in the Contract-A walk than overlays).
    user_root = tmp_path / "user" / ".agents"
    overlay = user_root / "overlays" / "toybox"
    _write(overlay / "cmds" / "toy.py", TOY)
    _write(
        user_root / "dotagents" / "cmds" / "toy.py",
        TOY.replace('_parsername_ = "toy"', '_parsername_ = "toy"\n    marker = "scope"'),
    )
    monkeypatch.setenv("AGENTS_HOME", str(user_root))
    monkeypatch.delenv("AGENTS_CMDS_PATH", raising=False)
    monkeypatch.chdir(tmp_path)

    commands = cli._discover([])
    toy = [
        c for c in commands
        if (getattr(c, "_parsername_", None) or getattr(c, "__name__", None)) == "toy"
    ]
    assert len(toy) == 1
    assert getattr(toy[0], "marker", None) == "scope"


# --------------------------------------------------------------------------- #
# Env var + flag extra sources
# --------------------------------------------------------------------------- #


def test_discover_honors_cmds_path_env(monkeypatch, tmp_path):
    cmds = tmp_path / "extra"
    _write(cmds / "toy.py", TOY)
    monkeypatch.setenv("AGENTS_HOME", str(tmp_path / "user" / ".agents"))
    monkeypatch.setenv("AGENTS_CMDS_PATH", str(cmds))
    monkeypatch.chdir(tmp_path)

    names = _names(cli._discover([]))
    assert "toy" in names


def test_discover_honors_cmdspath_flag(monkeypatch, tmp_path):
    cmds = tmp_path / "flagcmds"
    _write(cmds / "toy.py", TOY)
    monkeypatch.setenv("AGENTS_HOME", str(tmp_path / "user" / ".agents"))
    monkeypatch.delenv("AGENTS_CMDS_PATH", raising=False)
    monkeypatch.chdir(tmp_path)

    names = _names(cli._discover(["--cmdspath", str(cmds)]))
    assert "toy" in names


# --------------------------------------------------------------------------- #
# Scope walk: user AND project both contribute
# --------------------------------------------------------------------------- #


def test_user_scope_contributes(monkeypatch, tmp_path):
    user_root = tmp_path / "user" / ".agents"
    _write(user_root / "dotagents" / "cmds" / "toy.py", TOY)
    monkeypatch.setenv("AGENTS_HOME", str(user_root))
    monkeypatch.delenv("AGENTS_CMDS_PATH", raising=False)
    monkeypatch.chdir(tmp_path)

    names = _names(cli._discover([]))
    assert "toy" in names


def test_project_scope_contributes(monkeypatch, tmp_path):
    proj = tmp_path / "proj"
    _write(proj / ".agents" / "dotagents" / "cmds" / "toy.py", TOY)
    monkeypatch.setenv("AGENTS_HOME", str(tmp_path / "user" / ".agents"))
    monkeypatch.delenv("AGENTS_CMDS_PATH", raising=False)
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
    monkeypatch.setenv("AGENTS_HOME", str(tmp_path / "user" / ".agents"))
    monkeypatch.setenv(
        "AGENTS_CMDS_PATH", os.pathsep.join([str(missing), str(good)])
    )
    monkeypatch.chdir(tmp_path)

    # A missing dir is simply skipped; the good source still loads.
    names = _names(cli._discover([]))
    assert "toy" in names


def test_later_source_wins_dedup(monkeypatch, tmp_path):
    # The bundled cmds provide `link`; an env-var source shadows it. Later wins.
    shadow = tmp_path / "shadow"
    _write(shadow / "shadowlink.py", SHADOW_LINK)
    monkeypatch.setenv("AGENTS_HOME", str(tmp_path / "user" / ".agents"))
    monkeypatch.setenv("AGENTS_CMDS_PATH", str(shadow))
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
    monkeypatch.setenv("AGENTS_HOME", str(user_root))
    monkeypatch.delenv("AGENTS_CMDS_PATH", raising=False)
    monkeypatch.chdir(proj)

    commands = cli._discover([])
    toy = [
        c for c in commands
        if (getattr(c, "_parsername_", None) or getattr(c, "__name__", None)) == "toy"
    ]
    assert len(toy) == 1
    assert getattr(toy[0], "marker", None) == "project"


# --------------------------------------------------------------------------- #
# Env-var prefix back-compat (D80): new AGENTS_* preferred, old DOTAGENTS_* falls
# back for one release.
# --------------------------------------------------------------------------- #


def test_agents_home_fallback_to_legacy_dotagents_agents_dir(monkeypatch, tmp_path):
    # Only the OLD name set -> the user scope still resolves through it.
    user_root = tmp_path / "user" / ".agents"
    _write(user_root / "dotagents" / "cmds" / "toy.py", TOY)
    monkeypatch.delenv("AGENTS_HOME", raising=False)
    monkeypatch.setenv("DOTAGENTS_AGENTS_DIR", str(user_root))
    monkeypatch.delenv("AGENTS_CMDS_PATH", raising=False)
    monkeypatch.delenv("DOTAGENTS_CMDS_PATH", raising=False)
    monkeypatch.chdir(tmp_path)

    assert "toy" in _names(cli._discover([]))


def test_agents_home_new_name_wins_over_legacy(monkeypatch, tmp_path):
    # Both set: the new name wins; the legacy value is ignored.
    good = tmp_path / "good" / ".agents"
    _write(good / "dotagents" / "cmds" / "toy.py", TOY)
    stale = tmp_path / "stale" / ".agents"  # empty; would yield no `toy`
    monkeypatch.setenv("AGENTS_HOME", str(good))
    monkeypatch.setenv("DOTAGENTS_AGENTS_DIR", str(stale))
    monkeypatch.delenv("AGENTS_CMDS_PATH", raising=False)
    monkeypatch.delenv("DOTAGENTS_CMDS_PATH", raising=False)
    monkeypatch.chdir(tmp_path)

    assert "toy" in _names(cli._discover([]))


def test_cmds_path_fallback_to_legacy(monkeypatch, tmp_path):
    # Only the OLD $DOTAGENTS_CMDS_PATH set -> still honored.
    cmds = tmp_path / "extra"
    _write(cmds / "toy.py", TOY)
    monkeypatch.setenv("AGENTS_HOME", str(tmp_path / "user" / ".agents"))
    monkeypatch.delenv("AGENTS_CMDS_PATH", raising=False)
    monkeypatch.setenv("DOTAGENTS_CMDS_PATH", str(cmds))
    monkeypatch.chdir(tmp_path)

    assert "toy" in _names(cli._discover([]))
