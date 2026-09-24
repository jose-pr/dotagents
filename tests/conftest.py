"""Suite-wide isolation: every test runs against a throwaway home, store,
project and system root, with none of the running session's variables.

Without this, a test inherits whatever the shell that launched pytest
carries. From a dotagents-managed agent session that is a lot:
`AGENTS_PROJECT_ROOT` pinned to the checkout (its private `.agents/` then
leaks into "fresh install" assertions), `AGENTS_HOME` at the real store,
`AGENTS_RUNTIME_SET=1` (which turns the Codex PreToolUse hook into a no-op),
the harness markers (`CLAUDECODE`, ...) and a `PYTHONPATH` into an installed
overlay. And `Path.home()` is the developer's real home, where a forgotten
`config_root=` writes into the real `~/.claude`.

The autouse `_isolate` fixture below therefore, for EVERY test:

- points HOME and USERPROFILE (what `Path.home()` reads on Windows) at an
  empty per-test home, so `~/.agents`, `~/.claude`, `~/.codex`, ... are tmp;
- deletes every `AGENTS_*` / `DOTAGENTS_*` / `<NAME>_OVERLAY_ROOT` variable,
  `AGENT`, the harness project-root vars (`CLAUDE_PROJECT_DIR`), each
  adapter's detection markers and model vars, `CLAUDE_ENV_FILE`,
  `CODEX_HOME` and `PI_CODING_AGENT_DIR`;
- sets `AGENTS_SYSTEM_ROOT` to a path that does not exist (no system tier),
  and resets `_scope`'s per-value cache of it;
- sets `PYTHONPATH` to this tree's `src/`, so a subprocess running
  `-m dotagents` tests the same code the in-process tests import;
- chdirs into an empty per-test directory (the default project root);
- stops `ClaudeAgent`'s upward AGENTS.md walk at pytest's base temp dir, so
  no CLAUDE.md or AGENTS.md above it on the developer's disk is consulted.

A test that needs a real value sets it itself (monkeypatch), after this ran.

`_real_home_untouched` is the session-level backstop: it fingerprints the
files under the REAL home that dotagents writes, and fails the run if the
suite changed any of them.
"""

import hashlib
import os
from pathlib import Path

import pytest

from dotagents import _agents, _scope

SRC = Path(__file__).resolve().parents[1] / "src"

#: The developer's real home, captured at import -- before any fixture moves it.
REAL_HOME = Path.home()

_CLEARED_PREFIXES = ("AGENTS_", "DOTAGENTS_", "CLAUDE_CODE_", "CODEX_SANDBOX")
_CLEARED_SUFFIXES = ("_OVERLAY_ROOT",)


def _cleared_names() -> "set[str]":
    names = {"AGENT", "CLAUDE_ENV_FILE", "CODEX_HOME", "PI_CODING_AGENT_DIR"}
    names.update(_scope._HARNESS_PROJECT_ROOT_VARS)
    for agent in _agents.get_all_agents():
        names.update(agent.detect_env_vars)
        names.update(getattr(agent, "model_source_vars", None) or ())
    return names


_CLEARED_NAMES = _cleared_names()


def _is_session_var(name: str) -> bool:
    upper = name.upper()
    return (
        upper in _CLEARED_NAMES
        or upper.startswith(_CLEARED_PREFIXES)
        or upper.endswith(_CLEARED_SUFFIXES)
    )


@pytest.fixture(autouse=True)
def _isolate(tmp_path_factory, monkeypatch):
    sandbox = tmp_path_factory.mktemp("isolated")
    home = sandbox / "home"
    cwd = sandbox / "cwd"
    home.mkdir()
    cwd.mkdir()

    for name in list(os.environ):
        if _is_session_var(name):
            monkeypatch.delenv(name)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv(_scope.SYSTEM_ROOT_ENV, str(sandbox / "no-system-store"))
    monkeypatch.setattr(_scope, "_system_root_cache", {})
    monkeypatch.setenv("PYTHONPATH", str(SRC))
    monkeypatch.setattr(_agents.ClaudeAgent, "_walk_stop", tmp_path_factory.getbasetemp())
    monkeypatch.setattr(_scope, "_walk_stop", tmp_path_factory.getbasetemp())
    monkeypatch.chdir(cwd)
    saved_logging = _logging_state()
    yield home
    _restore_logging(saved_logging)


def _loggers():
    import logging

    yield logging.getLogger()
    for logger in list(logging.Logger.manager.loggerDict.values()):
        if isinstance(logger, logging.Logger):
            yield logger


def _logging_state():
    """Every logger's level/handlers/propagate/disabled. `cli.main` runs
    duho's logging setup -- levels on named loggers, a StreamHandler on the
    root bound to that test's captured stderr -- which would otherwise leak
    into later tests (a caplog assertion then sees INFO records it never
    used to)."""
    return {id(lg): (lg.level, list(lg.handlers), lg.propagate, lg.disabled) for lg in _loggers()}


def _restore_logging(saved):
    import logging

    root = logging.getLogger()
    for lg in _loggers():
        level, handlers, propagate, disabled = saved.get(id(lg), (logging.NOTSET, [], True, False))
        lg.setLevel(level)
        lg.propagate, lg.disabled = propagate, disabled
        if lg is root:
            # pytest attaches its own (StreamHandler subclass) capture handlers
            # to the root per phase and removes them itself: drop only plain
            # StreamHandlers added during the test.
            for h in list(root.handlers):
                if h not in handlers and type(h) is logging.StreamHandler:
                    root.removeHandler(h)
        else:
            lg.handlers[:] = handlers


#: Files and directories under the real home that dotagents writes to.
_GUARDED = (
    ".agents/AGENTS.md", ".agents/bin", ".agents/overlays", ".agents/skills",
    ".agents/findings", ".agents/.cache",
    ".claude/settings.json", ".claude/CLAUDE.md", ".claude/skills",
    ".codex/hooks.json", ".codex/config.toml", ".codex/hooks", ".codex/AGENTS.md",
    ".gemini/config/hooks.json", ".gemini/config/hooks", ".gemini/GEMINI.md",
    ".pi/agent",
)


def _fingerprint(root: Path) -> "dict[str, str]":
    out = {}
    for rel in _GUARDED:
        path = root / rel
        try:
            if path.is_file():
                out[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
            elif path.is_dir():
                out[rel] = "dir:" + ",".join(sorted(p.name for p in path.iterdir()))
            else:
                out[rel] = "absent"
        except OSError as exc:
            out[rel] = "unreadable:%s" % type(exc).__name__
    return out


@pytest.fixture(autouse=True, scope="session")
def _real_home_untouched():
    before = _fingerprint(REAL_HOME)
    yield
    after = _fingerprint(REAL_HOME)
    changed = sorted(rel for rel in before if before[rel] != after[rel])
    assert not changed, "the suite changed the real home: %s" % ", ".join(
        str(REAL_HOME / rel) for rel in changed
    )


@pytest.fixture
def bash_on_path(monkeypatch):
    """A working bash first on PATH, for product code that resolves `bash`
    from PATH (plain env-file sourcing). Skips where there is none -- and on
    Windows the WSL launcher stub (`WindowsApps`/`System32` `bash.exe`) does
    not count, see `_shell`."""
    from _shell import real_bash

    bash = real_bash()
    if bash is None:
        pytest.skip("needs a working bash")
    monkeypatch.setenv("PATH", str(Path(bash).parent) + os.pathsep + os.environ.get("PATH", ""))
    return bash
