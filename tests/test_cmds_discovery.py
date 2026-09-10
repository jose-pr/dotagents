"""Tests for command discovery (D76/D84): `dotagents.cli._discover` resolving the
built-in commands plus command modules from the bundled cmds dir, each installed
overlay's `cmds/`, the per-scope cmds dirs (user + project), `$AGENTS_CMDS_PATH`,
and `--cmdspath`.

dotagents bundles ONE command module, `findings`. Since D85 `link`/`sync` are
the private-sync overlay's `link-project`/`sync-project`, so the baseline
surface is the built-ins plus `findings`, and an OVERLAY's cmds dir is what adds
a private-sync command.

Filesystem-only (tmp_path); no network. NEVER exports HOME/USERPROFILE -- the
user scope is redirected via `$AGENTS_HOME` and the project scope via
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

# A command module that shadows the built-in `init` command, to prove
# later-source-wins dedup by _parsername_. (It used to shadow the bundled `link`;
# dotagents bundles no command module since D85, so a built-in is the target.)
SHADOW_INIT = '''\
"""A command module claiming the `init` name (shadow test)."""

from duho import Cmd, LoggingArgs


class ShadowInit(LoggingArgs, Cmd):
    """Shadow init."""

    _parsername_ = "init"

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


@pytest.fixture(autouse=True)
def _clear_project_root_vars(monkeypatch):
    """The project scope must come from `monkeypatch.chdir`, so the pinned
    project-root vars have to be cleared first (same as `test_scope.py`):
    `project_root_default()` reads `$AGENTS_PROJECT_ROOT`, then
    `$CLAUDE_PROJECT_DIR`, BEFORE the cwd. In an agent session dotagents' own
    env-loader hook pins `$AGENTS_PROJECT_ROOT` to the real checkout, and then
    the real repo's private `.agents/dotagents/cmds/` leaked into every
    "fresh install" assertion here (measured 2026-09-09: three failures from
    the harness's PowerShell tool, none from a shell without the hook)."""
    for var in ("AGENTS_PROJECT_ROOT", "CLAUDE_PROJECT_DIR"):
        monkeypatch.delenv(var, raising=False)


# --------------------------------------------------------------------------- #
# Baseline: built-ins + the bundled `findings` module, nothing else
# --------------------------------------------------------------------------- #


def test_discover_includes_builtins_only(monkeypatch, tmp_path):
    # Point both scopes at empty dirs so only built-ins + bundled cmds contribute.
    monkeypatch.setenv("AGENTS_HOME", str(tmp_path / "user" / ".agents"))
    monkeypatch.delenv("AGENTS_CMDS_PATH", raising=False)
    monkeypatch.chdir(tmp_path)

    names = _names(cli._discover([]))
    # The compiled built-ins survive the app switch.
    for builtin in ("init", "build-pyz", "context", "env", "overlays"):
        assert builtin in names
    # The one bundled command module: the findings queue. Its subcommands are
    # nested classes, so none of them leaks out as a top-level command.
    assert "findings" in names
    for nested in ("add", "list", "show", "done", "reopen", "remove", "index", "path"):
        assert nested not in names
    # D85: link/sync left the package. They are `link-project`/`sync-project`,
    # shipped by the opt-in private-sync overlay together with their logic, so a
    # plain dotagents (no overlay installed) offers no private-sync command.
    for gone in ("link", "sync", "link-project", "sync-project"):
        assert gone not in names
    # A personal command module is discovered only when the user has dropped it
    # into their own `<scope>/dotagents/cmds/` (D84) -- never a default of a
    # fresh install. So a fresh install offers EXACTLY the built-ins plus the
    # bundled findings module, and nothing else.
    assert "my-personal-tool" not in names
    assert set(names) == {"init", "build-pyz", "context", "env", "overlays", "findings"}
    # Built-ins are handed to `duho.app` via `commands=`, never `_subcommands_`.
    assert cli.Dotagents._subcommands_ == []


def test_bundled_cmds_dir_ships_only_findings(monkeypatch, tmp_path):
    # The bundled cmds DIR is a discovery source and `init` lays its README
    # down as the user's drop-in point. The only *.py it ships is `findings`
    # (link/sync left for the private-sync overlay, D85).
    bundled = cli._bundled_cmds_dir()
    assert bundled is not None and bundled.is_dir()
    assert [p.name for p in bundled.glob("*.py") if not p.name.startswith("_")] == ["findings.py"]


def test_overlay_supplies_link_project(monkeypatch, tmp_path):
    # The acceptance shape of D85: an installed overlay's cmds/ is what makes
    # `link-project` exist. (The real modules live on the overlays branch; this
    # asserts the discovery seam they plug into, with a stand-in module.)
    user_root = tmp_path / "user" / ".agents"
    _write(
        user_root / "overlays" / "private-sync" / "cmds" / "link_project.py",
        TOY.replace('_parsername_ = "toy"', '_parsername_ = "link-project"'),
    )
    monkeypatch.setenv("AGENTS_HOME", str(user_root))
    monkeypatch.delenv("AGENTS_CMDS_PATH", raising=False)
    monkeypatch.chdir(tmp_path)

    assert "link-project" in _names(cli._discover([]))


def test_installed_overlay_cmds_are_discovered(monkeypatch, tmp_path):
    # An installed overlay shipping a command at <overlay-root>/cmds/*.py is
    # discovered (D84 per-overlay cmds via the Scope.paths Contract-A walk).
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
    # A nonexistent dir + a syntactically-broken command file + one that raises
    # at import time must not crash: discovery runs before EVERY invocation,
    # including `env`/`context` inside the SessionStart hooks, so one typo in a
    # personal cmds module used to take the whole session's env and context
    # down (review 2026-09-09). duho propagates SyntaxError on purpose; the
    # dotagents layer is where it must stop.
    good = tmp_path / "good"
    _write(good / "toy.py", TOY)
    broken = tmp_path / "broken"
    _write(broken / "typo.py", BROKEN)
    raising = tmp_path / "raising"
    _write(raising / "boom.py", "raise RuntimeError('import-time failure')\n")
    # SystemExit is NOT an Exception: a module that sys.exit()s at import (a
    # project-scope override refusing to load without its user-scope base,
    # measured 2026-09-10 installing into a fresh Linux home from a checkout
    # whose private cmds/ carried one) killed `init -g` before any store existed.
    exiting = tmp_path / "exiting"
    _write(exiting / "refuse.py", "raise SystemExit('error: install the base tool first')\n")
    missing = tmp_path / "does-not-exist"
    monkeypatch.setenv("AGENTS_HOME", str(tmp_path / "user" / ".agents"))
    monkeypatch.setenv(
        "AGENTS_CMDS_PATH",
        os.pathsep.join([str(missing), str(broken), str(raising), str(exiting), str(good)]),
    )
    monkeypatch.chdir(tmp_path)

    # Every bad source is skipped; the good one still loads, as do built-ins.
    names = _names(cli._discover([]))
    assert "toy" in names
    assert "env" in names and "context" in names


def test_a_bad_module_does_not_take_its_siblings_down(monkeypatch, tmp_path, caplog):
    """Resilience is per MODULE, not per directory: duho's own loop lets a
    SyntaxError/RuntimeError/SystemExit escape and would discard every command
    already collected from the same dir. The warning names the FILE."""
    import logging

    cmds = tmp_path / "cmds"
    _write(cmds / "aa_toy.py", TOY)
    _write(cmds / "bb_refuse.py", "raise SystemExit('install the base tool first')\n")
    _write(cmds / "cc_typo.py", BROKEN)
    _write(cmds / "dd_silent_exit.py", "import sys\nsys.exit()\n")
    monkeypatch.setenv("AGENTS_HOME", str(tmp_path / "user" / ".agents"))
    monkeypatch.setenv("AGENTS_CMDS_PATH", str(cmds))
    monkeypatch.chdir(tmp_path)

    with caplog.at_level(logging.WARNING, logger="dotagents.cli"):
        names = _names(cli._discover([]))
    assert "toy" in names, "the sibling in the same directory survives"
    messages = [r.getMessage() for r in caplog.records]
    assert any("bb_refuse.py" in m and "install the base tool first" in m for m in messages), messages
    assert any("cc_typo.py" in m and "SyntaxError" in m for m in messages), messages
    assert any("dd_silent_exit.py" in m and "no message" in m for m in messages), messages


def test_later_source_wins_dedup(monkeypatch, tmp_path):
    # `init` is a compiled built-in (the earliest source); an env-var source
    # claims the same name. Later wins.
    shadow = tmp_path / "shadow"
    _write(shadow / "shadowinit.py", SHADOW_INIT)
    monkeypatch.setenv("AGENTS_HOME", str(tmp_path / "user" / ".agents"))
    monkeypatch.setenv("AGENTS_CMDS_PATH", str(shadow))
    monkeypatch.chdir(tmp_path)

    commands = cli._discover([])
    init_cmds = [
        c for c in commands
        if (getattr(c, "_parsername_", None) or getattr(c, "__name__", None)) == "init"
    ]
    # Exactly one `init` in the resolved set (dedup), and it is the shadow.
    assert len(init_cmds) == 1
    assert init_cmds[0].__name__ == "ShadowInit"


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


