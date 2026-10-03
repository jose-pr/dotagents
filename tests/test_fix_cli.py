"""Regression tests for CLI fixes: command discovery that survives a
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

from dotagents import cli
from _helpers import run_cmd as _run

ROOT = Path(__file__).resolve().parents[1]

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
        # What a duho release that renamed its remaining private helper looks
        # like to `from duho.discovery import _commands_in_module, ...` --
        # and only to it. `import_from_path` is duho's public counterpart
        # (duho >= 0.6.0) and stays importable; `_commands_in_module` has no
        # public counterpart yet, so it is the one still exposed to this risk.
        if name == "duho.discovery" and "_commands_in_module" in (fromlist or ()):
            raise ImportError("cannot import name '_commands_in_module'")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", renamed)
    with caplog.at_level(logging.WARNING):
        commands = cli._discover_modules(cmds)
    assert "good" in _names(commands)
    assert any("internals moved" in r.getMessage() for r in caplog.records)


# --------------------------------------------------------------------------- #
# The zipapp-repointed module list is derived, not hand-kept
# --------------------------------------------------------------------------- #


def test_command_modules_cover_every_builtin_and_its_bases():
    wanted = set()
    pending = [cli.Dotagents, *cli._BUILTIN_COMMANDS]
    while pending:
        command = pending.pop()
        wanted.update(
            k.__module__ for k in command.__mro__
            if k.__module__.startswith(("dotagents.", "duho."))
        )
        pending.extend(getattr(command, "_subcommands_", None) or [])
    assert wanted <= set(cli._COMMAND_MODULES)
    assert {"dotagents.cli", "dotagents.cli._common", "duho.presets"} <= set(cli._COMMAND_MODULES)


def test_a_new_builtin_module_is_covered_without_a_second_list():
    New = type("New", (cli.DotAgentsArgs,), {"__module__": "dotagents.cli.brand_new"})
    Leaf = type("Leaf", (cli.DotAgentsArgs,), {"__module__": "dotagents.cli.leaf"})
    New._subcommands_ = [Leaf]
    modules = cli._command_modules([New])
    assert {"dotagents.cli.brand_new", "dotagents.cli.leaf", "dotagents.cli._common",
            "duho.presets"} <= set(modules)


# --------------------------------------------------------------------------- #
# Help summaries
# --------------------------------------------------------------------------- #


def _summary(command) -> str:
    doc = (command.__doc__ or "").strip()
    return doc.splitlines()[0] if doc else ""


@pytest.mark.parametrize(
    "name",
    [
        "init", "build-pyz", "context", "env", "overlays", "about", "findings", "launch",
    ],
)
def test_every_command_summary_is_a_whole_sentence(name):
    """duho shows a docstring's first physical line as the summary in
    `dotagents --help`; a sentence wrapped onto a second line shows cut off."""
    commands = cli._discover([])
    command = dict(zip(_names(commands), commands))[name]
    assert _summary(command).endswith("."), _summary(command)


def test_every_overlays_subcommand_summary_is_a_whole_sentence():
    from dotagents.cli import overlays

    subs = overlays.Overlays._subcommands_
    assert subs
    for sub in subs:
        assert _summary(sub).endswith("."), (sub, _summary(sub))


def test_every_findings_subcommand_summary_is_a_whole_sentence(findings_mod):
    for sub in findings_mod.Findings._subcommands_:
        assert _summary(sub).endswith("."), (sub, _summary(sub))


# --------------------------------------------------------------------------- #
# findings: encodings
# --------------------------------------------------------------------------- #


def test_a_non_utf8_note_is_skipped_and_left_alone(findings_mod, tmp_path, capsys, caplog):
    F = findings_mod.Findings
    d = tmp_path / "q"
    d.mkdir()
    note = d / "handnote.md"
    raw = b"caf\xe9 note\n"
    note.write_bytes(raw)

    with caplog.at_level(logging.WARNING):
        assert _run(F.Add, description="second finding", dir=d) == 0
        assert _run(F.List, dir=d) == 0
    out = capsys.readouterr().out
    assert "second-finding: second finding" in out
    assert note.read_bytes() == raw, "never rewritten"
    assert any("handnote.md" in r.getMessage() and "not UTF-8" in r.getMessage()
               for r in caplog.records)


def test_a_bom_does_not_hide_the_frontmatter(findings_mod, tmp_path):
    F = findings_mod.Findings
    d = tmp_path / "q"
    d.mkdir()
    (d / "bommed.md").write_bytes(
        b"\xef\xbb\xbf---\nname: bommed\ndescription: written by PowerShell 5\n"
        b"status: active\n---\n\nthe details\n"
    )
    finding = findings_mod.FindingsStore(d).get("bommed")
    assert finding.description == "written by PowerShell 5"

    _run(F.Done, name="bommed", resolution="fixed", dir=d)
    text = (d / "processed" / "bommed.md").read_bytes().decode("utf-8")
    assert text.startswith("---\nname: bommed\n")
    assert text.count("---\n") == 2, "one frontmatter, not one nested in the body"


def test_stdin_is_decoded_as_utf8_whatever_the_locale(findings_mod, tmp_path, monkeypatch):
    F = findings_mod.Findings
    d = tmp_path / "q"
    piped = "arrow \u2192 and caf\u00e9\n"
    monkeypatch.setattr(
        sys, "stdin", io.TextIOWrapper(io.BytesIO(piped.encode("utf-8")), encoding="cp1252")
    )
    _run(F.Add, description="third", body_file=Path("-"), dir=d)
    assert piped.strip() in (d / "third.md").read_text(encoding="utf-8")


def test_a_non_utf8_body_file_is_a_clean_error(findings_mod, tmp_path):
    F = findings_mod.Findings
    body = tmp_path / "body.md"
    body.write_bytes(b"caf\xe9\n")
    with pytest.raises(SystemExit, match="not UTF-8"):
        _run(F.Add, description="x", body_file=body, dir=tmp_path / "q")


# --------------------------------------------------------------------------- #
# findings: names, moves, removal, output
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("kwargs", [{"description": "Index"}, {"description": "x", "name": "README"}])
def test_reserved_slugs_are_refused(findings_mod, tmp_path, kwargs):
    d = tmp_path / "q"
    with pytest.raises(SystemExit, match="reserved"):
        _run(findings_mod.Findings.Add, dir=d, **kwargs)
    assert not d.exists() or not any(d.iterdir())


def test_index_and_readme_are_never_findings_in_any_case(findings_mod, tmp_path):
    d = tmp_path / "q"
    d.mkdir()
    (d / "readme.md").write_text("# About this queue\n", encoding="utf-8")
    (d / "real.md").write_text("# A real one\n", encoding="utf-8")
    assert [f.name for f in findings_mod.FindingsStore(d).active()] == ["real"]


def test_done_never_overwrites_a_processed_record(findings_mod, tmp_path):
    d = tmp_path / "q"
    (d / "processed").mkdir(parents=True)
    old = d / "processed" / "dup.md"
    old.write_text("---\nname: dup\nstatus: processed\n---\n\n## Resolution\n\nkept\n",
                   encoding="utf-8")
    (d / "dup.md").write_text("# a hand-written note reusing the stem\n", encoding="utf-8")
    before_old, before_new = old.read_bytes(), (d / "dup.md").read_bytes()

    with pytest.raises(SystemExit, match="already exists"):
        findings_mod.FindingsStore(d).done("dup", "resolved")
    assert old.read_bytes() == before_old
    assert (d / "dup.md").read_bytes() == before_new


def test_reopen_never_overwrites_an_active_finding(findings_mod, tmp_path):
    d = tmp_path / "q"
    (d / "processed").mkdir(parents=True)
    (d / "processed" / "y.md").write_text(
        "---\nname: yy\nstatus: processed\n---\n\nold\n", encoding="utf-8"
    )
    active = d / "y.md"
    active.write_text("---\nname: other\nstatus: active\n---\n\nnew\n", encoding="utf-8")
    before = active.read_bytes()

    with pytest.raises(SystemExit, match="already exists"):
        findings_mod.FindingsStore(d).reopen("yy")
    assert active.read_bytes() == before
    assert (d / "processed" / "y.md").is_file()


def test_remove_refuses_a_processed_finding(findings_mod, tmp_path):
    store = findings_mod.FindingsStore(tmp_path / "q")
    store.add("to be processed", name="p")
    store.done("p", "resolved")
    with pytest.raises(SystemExit, match="reopen"):
        store.remove("p")
    assert (tmp_path / "q" / "processed" / "p.md").is_file()


def test_paths_print_as_utf8_on_a_legacy_console(findings_mod, tmp_path, monkeypatch):
    raw = io.BytesIO()
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(raw, encoding="cp1252"))
    d = tmp_path / "q\u2192"
    assert _run(findings_mod.Findings.Add, description="arrow dir", dir=d) == 0
    assert raw.getvalue().decode("utf-8").strip() == str(d / "arrow-dir.md")


# --------------------------------------------------------------------------- #
# launch
# --------------------------------------------------------------------------- #


def _program(tmp_path, name="fake-harness", suffix=None):
    """An executable file `shutil.which` accepts, in a dir of its own."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    if os.name == "nt":
        p = bin_dir / (name + (suffix or ".cmd"))
        p.write_text("@echo off\r\n")
    else:
        p = bin_dir / name
        p.write_text("#!/bin/sh\n")
        p.chmod(p.stat().st_mode | stat.S_IXUSR)
    return p


def _capture_spawn(monkeypatch, launch_mod):
    calls = []
    monkeypatch.setattr(launch_mod, "_spawn", lambda argv, env: calls.append(list(argv)) or 0)
    return calls


def _context_is(monkeypatch, text):
    from dotagents import _context

    monkeypatch.setattr(_context, "assemble_context", lambda agent, scope, inline=False: text)


def test_a_signal_killed_harness_exits_128_plus_n(launch_mod, monkeypatch):
    class Proc:
        def wait(self):
            return -2

    monkeypatch.setattr(launch_mod.subprocess, "Popen", lambda argv, env: Proc())
    assert launch_mod._spawn(["x"], {}) == 130


def test_the_context_file_is_stable_and_outside_temp(launch_mod, monkeypatch, tmp_path):
    program = _program(tmp_path)
    _context_is(monkeypatch, "# rules\n")
    calls = _capture_spawn(monkeypatch, launch_mod)

    _run(launch_mod.Launch, passthrough=[], agent="claude", command=str(program))
    _run(launch_mod.Launch, passthrough=[], agent="claude", command=str(program))

    first, second = (Path(argv[2]) for argv in calls)
    assert first == second, "each launch overwrites the same file"
    cache = tmp_path / "store" / ".cache" / "launch"
    assert first.parent == cache
    assert not str(first).startswith(tempfile.gettempdir() + os.sep + "dotagents-")
    assert first.read_text(encoding="utf-8") == "# rules\n"
    assert (cache / ".gitignore").read_text(encoding="utf-8") == "*\n"


@pytest.mark.skipif(os.name != "nt", reason="cmd.exe re-parsing is Windows-only")
@pytest.mark.parametrize("arg", ["x&whoami", "say 100%USERNAME%", "a|b", "line\nbreak", 'q"uote'])
def test_cmd_shim_arguments_cmd_exe_would_rewrite_are_refused(launch_mod, monkeypatch, tmp_path, arg):
    program = _program(tmp_path)
    calls = _capture_spawn(monkeypatch, launch_mod)
    with pytest.raises(SystemExit, match="cmd.exe"):
        _run(launch_mod.Launch, passthrough=["-p", arg], agent="claude",
             command=str(program), no_context=True)
    assert calls == []


@pytest.mark.skipif(os.name != "nt", reason="cmd.exe re-parsing is Windows-only")
def test_plain_arguments_and_real_executables_are_untouched(launch_mod, monkeypatch, tmp_path):
    calls = _capture_spawn(monkeypatch, launch_mod)
    shim = _program(tmp_path)
    _run(launch_mod.Launch, passthrough=["-p", "hello world"], agent="claude",
         command=str(shim), no_context=True)
    exe = _program(tmp_path, name="real-harness", suffix=".exe")
    _run(launch_mod.Launch, passthrough=["-p", "x&y 100%"], agent="claude",
         command=str(exe), no_context=True)
    assert [argv[1:] for argv in calls] == [["-p", "hello world"], ["-p", "x&y 100%"]]
    assert launch_mod._via_cmd_exe(r"C:\x\CLAUDE.CMD") and launch_mod._via_cmd_exe("a.bat")
    assert not launch_mod._via_cmd_exe(r"C:\x\node.exe")
