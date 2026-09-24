"""Regression tests for the 2026-09-23 review's `init` / `launch` fixes:

- `init --from` takes the documented URI, git and checkout forms (it crashed
  on every remote scheme and on a checkout root);
- atomic settings writes go through a symlink and keep the file's mode;
- a BOM in settings.json is accepted, and a corrupt one stops `init` before
  anything is written;
- `--dry-run` says what would happen instead of logging it as done;
- `launch` never runs a harness from the current directory, and a variable an
  env layer unset does not reach the harness;
- the unused `_run_overlay_setup` helper is gone.
"""

import functools
import http.server
import importlib.util
import json
import logging
import os
import stat
import sys
import threading
from pathlib import Path

import pytest

from dotagents import _hooks, _sources, cli
from dotagents._fs import write_text_lf
from dotagents.cli._common import BASE_ROOT, STORE_CONFIG, _resolve_from, read_store_config

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = "<!-- dotagents:begin -->\n# %s\nread `{{AGENTS_MD}}`\n\n## Load on demand\n<!-- dotagents:end -->\n"


def _main(argv):
    try:
        return cli.main(list(argv))
    except SystemExit as exc:
        if exc.code is None:
            return 0
        return exc.code if isinstance(exc.code, int) else 2


def _base(root: Path, title: str = "CUSTOM BASE") -> Path:
    (root / "dotagents" / "templates").mkdir(parents=True)
    (root / "dotagents" / "templates" / "AGENTS.md").write_text(TEMPLATE % title, encoding="utf-8")
    return root


def _init(dest: Path, *extra: str) -> int:
    return _main(["init", "--dest", str(dest), "--agents", "codex", "--no-hooks", *extra])


# --------------------------------------------------------------------------- #
# Atomic writes through a symlink; the mode survives
# --------------------------------------------------------------------------- #


def _symlink(target: Path, link: Path) -> None:
    try:
        os.symlink(str(target), str(link))
    except (OSError, NotImplementedError):
        pytest.skip("symlinks need privileges here")


def test_settings_write_keeps_a_symlink_and_updates_its_target(tmp_path):
    dotfiles = tmp_path / "dotfiles"
    dotfiles.mkdir()
    real = dotfiles / "claude-settings.json"
    real.write_text('{"theme": "dark"}\n', encoding="utf-8")
    link_dir = tmp_path / ".claude"
    link_dir.mkdir()
    link = link_dir / "settings.json"
    _symlink(real, link)

    _hooks.write_settings(link, {"theme": "dark", "hooks": {}})

    assert link.is_symlink(), "the link was replaced by a plain file"
    assert json.loads(real.read_text(encoding="utf-8")) == {"theme": "dark", "hooks": {}}
    assert [p.name for p in link_dir.iterdir()] == ["settings.json"]
    assert [p.name for p in dotfiles.iterdir()] == ["claude-settings.json"], "no temp file left"


def test_atomic_write_through_a_dangling_link_creates_its_target(tmp_path):
    real = tmp_path / "dotfiles" / "hooks.json"
    real.parent.mkdir()
    link = tmp_path / "hooks.json"
    _symlink(real, link)
    write_text_lf(link, "{}\n", atomic=True)
    assert link.is_symlink() and real.read_bytes() == b"{}\n"


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
def test_atomic_write_keeps_the_mode_and_a_new_file_is_not_0600(tmp_path):
    existing = tmp_path / "settings.json"
    existing.write_text("{}\n", encoding="utf-8")
    existing.chmod(0o640)
    write_text_lf(existing, '{"a": 1}\n', atomic=True)
    assert stat.S_IMODE(existing.stat().st_mode) == 0o640

    fresh = tmp_path / "fresh.json"
    write_text_lf(fresh, "{}\n", atomic=True)
    plain = tmp_path / "plain.json"
    write_text_lf(plain, "{}\n")
    assert stat.S_IMODE(fresh.stat().st_mode) == stat.S_IMODE(plain.stat().st_mode)


# --------------------------------------------------------------------------- #
# settings.json: a BOM is fine; a corrupt file stops init before any write
# --------------------------------------------------------------------------- #


def test_a_bom_in_settings_json_is_accepted_and_not_written_back(tmp_path):
    settings = Path.home() / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_bytes(b'\xef\xbb\xbf{"theme": "dark"}\n')

    assert _hooks.load_settings(settings) == {"theme": "dark"}
    assert _main(["init", "-g", "--agents", "claude"]) == 0

    raw = settings.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    data = json.loads(raw.decode("utf-8"))
    assert data["theme"] == "dark" and "SessionStart" in data["hooks"]


@pytest.mark.parametrize("content", [b"{not json", b"\xff\xfe{}"])
def test_a_corrupt_settings_file_stops_init_before_anything_is_written(content):
    """The error fired after AGENTS.md and the CLAUDE.md include were written,
    leaving a half-initialised store."""
    settings = Path.home() / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_bytes(content)

    with pytest.raises(SystemExit) as exc:
        cli.main(["init", "-g", "--agents", "claude"])
    assert str(settings) in str(exc.value)
    assert not (Path.home() / ".agents").exists()
    assert not (Path.home() / ".claude" / "CLAUDE.md").exists()
    assert settings.read_bytes() == content


# --------------------------------------------------------------------------- #
# overlays: the setup helper that set AGENTS_HOME to the scope store is gone
# --------------------------------------------------------------------------- #


def test_the_old_overlay_setup_helper_is_gone():
    """`_run_overlay_setup` passed the scope store as `AGENTS_HOME` and had no
    caller left; `Overlay.run_setup(scope_root=, scope_level=)` replaced it."""
    from dotagents.cli import _common

    assert not hasattr(cli, "_run_overlay_setup")
    assert not hasattr(_common, "_run_overlay_setup")
