"""Regression tests for the CLI review fixes: command discovery that survives a
command whose parser cannot be built, the `findings` queue's encoding, naming
and move-never-delete rules, and `launch`'s exit code, context file and
cmd.exe argument handling.

Filesystem-only (tmp_path). The user store is redirected with `$AGENTS_HOME`
and the project with `monkeypatch.chdir`; HOME is never exported, so a real
`~/.agents` is never touched.
"""

import importlib.util
import io
import logging
import os
import stat
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dotagents import cli  # noqa: E402

CMDS = ROOT / "src" / "dotagents" / "_overlay" / "dotagents" / "cmds"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def findings_mod():
    try:
        yield _load("test_fix_cli_findings", CMDS / "findings.py")
    finally:
        sys.modules.pop("test_fix_cli_findings", None)


@pytest.fixture(scope="module")
def launch_mod():
    try:
        yield _load("test_fix_cli_launch", CMDS / "launch.py")
    finally:
        sys.modules.pop("test_fix_cli_launch", None)


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    """A fresh store and project, no harness markers, and os.environ restored
    afterwards (`launch` exports the assembled env into this process)."""
    saved = dict(os.environ)
    for var in (
        "AGENTS_PROJECT_ROOT", "CLAUDE_PROJECT_DIR", "AGENTS_HOME", "AGENTS_CMDS_PATH",
        "AGENTS_HARNESS", "AGENTS_CONTEXT_FILE", "CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT",
        "GEMINI_CLI", "CODEX_SANDBOX",
    ):
        monkeypatch.delenv(var, raising=False)
    store = tmp_path / "store"
    store.mkdir()
    monkeypatch.setenv("AGENTS_HOME", str(store))
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)
    yield tmp_path
    os.environ.clear()
    os.environ.update(saved)


def _run(cmd_cls, passthrough=None, **kwargs):
    cmd = cmd_cls()
    if passthrough is not None:
        cmd._passthrough_ = list(passthrough)
    for k, v in kwargs.items():
        setattr(cmd, k, v)
    return cmd()


def _names(commands):
    return [getattr(c, "_parsername_", None) or getattr(c, "__name__", None) for c in commands]


# --------------------------------------------------------------------------- #
# Discovery: a command that imports but cannot build its parser
# --------------------------------------------------------------------------- #

GOOD = '''\
"""A good command."""
from duho import Cmd, LoggingArgs


class Good(LoggingArgs, Cmd):
    """A good command."""
    _parsername_ = "good"

    def __call__(self) -> int:
        return 0
'''

UNRESOLVED_ANNOTATION = '''\
"""A command whose annotation names something never imported."""
from __future__ import annotations

from typing import Optional

from duho import Cmd, LoggingArgs


class Unresolved(LoggingArgs, Cmd):
    """Broken."""
    _parsername_ = "unresolved"

    out: Optional[Path] = None
    ("--out",)

    def __call__(self) -> int:
        return 0


class Sibling(LoggingArgs, Cmd):
    """Same file, nothing wrong with it."""
    _parsername_ = "sibling"

    def __call__(self) -> int:
        return 0
'''

DUPLICATE_FLAG = '''\
"""Two fields claiming the same option."""
from duho import Cmd, LoggingArgs


class Dup(LoggingArgs, Cmd):
    """Broken."""
    _parsername_ = "dup"

    a: str = ""
    ("--same",)

    b: str = ""
    ("--same",)

    def __call__(self) -> int:
        return 0
'''

PEP604 = '''\
"""`X | None` annotations, which Python 3.9 cannot evaluate."""
from __future__ import annotations

from pathlib import Path

from duho import Cmd, LoggingArgs


class Pep(LoggingArgs, Cmd):
    """PEP 604."""
    _parsername_ = "pep"

    out: Path | None = None
    ("--out",)

    def __call__(self) -> int:
        return 0
'''


@pytest.mark.parametrize(
    "stem, source, broken",
    [
        ("unresolved", UNRESOLVED_ANNOTATION, True),
        ("dup", DUPLICATE_FLAG, True),
        ("pep", PEP604, sys.version_info < (3, 10)),
    ],
    ids=["unresolved-annotation", "duplicate-flag", "pep604"],
)
def test_an_unbuildable_command_does_not_take_every_command_down(
    monkeypatch, tmp_path, capsys, caplog, stem, source, broken
):
    cmds = tmp_path / "cmds"
    cmds.mkdir()
    (cmds / "aa_good.py").write_text(GOOD, encoding="utf-8")
    (cmds / (stem + ".py")).write_text(source, encoding="utf-8")
    monkeypatch.setenv("AGENTS_CMDS_PATH", str(cmds))

    with caplog.at_level(logging.WARNING):
        names = _names(cli._discover([]))
    assert "good" in names
    assert (stem in names) is not broken
    if broken:
        messages = [r.getMessage() for r in caplog.records]
        assert any(stem + ".py" in m and "skipping command" in m for m in messages), messages
    if stem == "unresolved":
        assert "sibling" in names, "the healthy class in the same file survives"

    # ... and the process as a whole still runs.
    capsys.readouterr()
    assert cli.main(["about"]) == 0
    assert capsys.readouterr().out.startswith("dotagents-cli ")


def test_a_command_clashing_with_an_umbrella_flag_is_skipped(monkeypatch, tmp_path, caplog):
    cmds = tmp_path / "cmds"
    cmds.mkdir()
    (cmds / "clash.py").write_text(
        GOOD.replace('"good"', '"clash"').replace(
            "    def __call__", '    extra: str = ""\n    ("--cmdspath",)\n\n    def __call__'
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENTS_CMDS_PATH", str(cmds))
    with caplog.at_level(logging.WARNING):
        assert "clash" not in _names(cli._discover([]))
    assert cli.main(["about"]) == 0


def test_moved_duho_internals_are_reported_not_silent(monkeypatch, tmp_path, caplog):
    import builtins

    cmds = tmp_path / "cmds"
    cmds.mkdir()
    (cmds / "good.py").write_text(GOOD, encoding="utf-8")
    real_import = builtins.__import__

    def renamed(name, globals=None, locals=None, fromlist=(), level=0):
        # What a duho release that renamed its private helpers looks like to
        # `from duho.discovery import _import_from_path, ...` -- and only to it.
        if name == "duho.discovery" and "_import_from_path" in (fromlist or ()):
            raise ImportError("cannot import name '_import_from_path'")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", renamed)
    with caplog.at_level(logging.WARNING):
        commands = cli._discover_modules(cmds)
    assert "good" in _names(commands)
    assert any("internals moved" in r.getMessage() for r in caplog.records)
