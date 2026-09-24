"""Overlay `lib/` dirs reach everything an overlay starts: a setup script run
from a plain shell, and a command module, import another overlay's library.
(The session's own PYTHONPATH is covered in test_env.py / test_fix_env.py.)

tmp dirs only; the conftest isolates the home and the stores.
"""

import os
import sys

from dotagents import cli
from dotagents.cli import OverlayAdd

from _helpers import run_cmd as _run

BASE_AGENTS = "<!-- dotagents:begin -->\n# BASE\n## Load on demand\n<!-- dotagents:end -->\n"


def _overlay(src, name, *, requires=(), files=()):
    ov = src / name
    ov.mkdir(parents=True)
    (ov / "overlay.toml").write_text(
        'name = "%s"\nrequires = [%s]\n' % (name, ", ".join('"%s"' % r for r in requires)),
        encoding="utf-8",
    )
    for rel, body in files:
        (ov / rel).parent.mkdir(parents=True, exist_ok=True)
        (ov / rel).write_text(body, encoding="utf-8")
    return ov


def test_a_setup_script_imports_a_required_overlays_lib_and_sees_its_bin(tmp_path, monkeypatch):
    """`overlays add` from a plain shell (no session env): the setup script got
    `os.environ` as it was, so an overlay built on another's lib failed."""
    monkeypatch.delenv("PYTHONPATH", raising=False)
    monkeypatch.delenv("AGENTS_PYTHONPATH", raising=False)
    src, store = tmp_path / "src", tmp_path / "store"
    store.mkdir()
    (store / "AGENTS.md").write_text(BASE_AGENTS, encoding="utf-8")
    marker = tmp_path / "setup-saw.txt"
    _overlay(src, "base", files=[("lib/base_helpers.py", "VALUE = 'from base'\n"),
                                 ("bin/base-tool", "#!/bin/sh\n")])
    _overlay(src, "consumer", requires=["base"], files=[("setup.py", (
        "import os, base_helpers\n"
        "open(%r, 'w').write(base_helpers.VALUE + '|' + os.environ['PATH'])\n" % str(marker)
    ))])
    rc = _run(OverlayAdd, name=["consumer"], repo=[str(src)], global_scope=True,
              agents_dir=store, copy=True, dry_run=False)
    assert rc == 0
    value, path = marker.read_text(encoding="utf-8").split("|", 1)
    assert value == "from base"
    assert str(store / "overlays" / "base" / "bin") in path.split(os.pathsep)


def test_a_command_module_imports_an_overlays_lib_from_a_plain_shell(tmp_path, monkeypatch):
    monkeypatch.delenv("PYTHONPATH", raising=False)
    store = tmp_path / "store"
    lib = store / "overlays" / "tools" / "lib"
    cmds = store / "overlays" / "tools" / "cmds"
    lib.mkdir(parents=True)
    cmds.mkdir()
    (store / "overlays" / "tools" / "overlay.toml").write_text('name = "tools"\n', encoding="utf-8")
    (lib / "tools_lib_probe.py").write_text("NAME = 'probe'\n", encoding="utf-8")
    (cmds / "probe.py").write_text(
        "from duho import Cmd, LoggingArgs\n"
        "import tools_lib_probe\n\n\n"
        "class Probe(LoggingArgs, Cmd):\n"
        '    """Print the lib value."""\n'
        '    _parsername_ = "probe"\n\n'
        "    def __call__(self) -> int:\n"
        "        return 0\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENTS_HOME", str(store))
    saved = list(sys.path)
    try:
        names = [getattr(c, "_parsername_", None) for c in cli._discover([])]
        assert "probe" in names
        assert sys.path.index(str(lib)) > 0 and str(lib) not in saved
    finally:
        sys.path[:] = saved
        sys.modules.pop("tools_lib_probe", None)
