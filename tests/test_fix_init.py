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
# init --from: checkout roots, URIs, git -- one resolver, clean errors
# --------------------------------------------------------------------------- #


def test_from_a_dotagents_checkout_uses_its_bundled_base(tmp_path):
    """A checkout root used to fail with FileNotFoundError: the template lives
    under src/dotagents/_overlay, not at the root."""
    assert _resolve_from(str(ROOT), BASE_ROOT) == ROOT / "src" / "dotagents" / "_overlay"
    dest = tmp_path / "store"
    assert _init(dest, "--from", str(ROOT)) == 0
    assert "## Always-on rules" in (dest / "AGENTS.md").read_text(encoding="utf-8")
    assert read_store_config(dest) == {"base": str(ROOT.resolve())}


def test_from_a_dir_that_is_no_base_is_a_usage_error(tmp_path, capsys):
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(SystemExit) as exc:
        _resolve_from(str(empty), BASE_ROOT)
    assert "is not a base overlay" in str(exc.value)
    assert _init(tmp_path / "store", "--from", str(empty)) != 0
    assert not (tmp_path / "store" / "AGENTS.md").exists()


def test_from_a_file_uri_is_the_local_dir(tmp_path):
    base = _base(tmp_path / "b")
    dest = tmp_path / "store"
    assert _init(dest, "--from", base.resolve().as_uri()) == 0
    assert "# CUSTOM BASE" in (dest / "AGENTS.md").read_text(encoding="utf-8")


def _stub_scheme(monkeypatch, served: Path, seen: list):
    """`stub://` materializes to ``served`` (the remote half is `_sources`'
    own business, tested there); records each spec it was asked for."""
    real = _sources.SourceCache.materialize

    def materialize(self, spec):
        if _sources.url_scheme(spec.location) != "stub":
            return real(self, spec)
        seen.append((spec, self.root))
        if "missing" in spec.location:
            raise _sources.SourceError("error: %s does not exist" % spec.display())
        return served

    monkeypatch.setattr(_sources.SourceCache, "materialize", materialize)


def test_from_a_remote_scheme_goes_through_the_overlay_source_cache(tmp_path, monkeypatch, caplog):
    """Every remote scheme crashed in `Path(UriPath(...))` (`fspath for https`);
    `--from` now materializes the way `overlays` sources do, into the user
    store's overlay cache, and records the location without its credentials."""
    served = _base(tmp_path / "served")
    seen: list = []
    _stub_scheme(monkeypatch, served, seen)
    monkeypatch.setenv("AGENTS_HOME", str(tmp_path / "user"))
    dest = tmp_path / "store"
    with caplog.at_level(logging.WARNING):
        assert _init(dest, "--from", "stub://alice:s3cret@host/base") == 0
    assert "# CUSTOM BASE" in (dest / "AGENTS.md").read_text(encoding="utf-8")
    (spec, cache_root), = seen
    assert spec.kind == "url" and cache_root == tmp_path / "user" / ".cache" / "overlays"
    assert read_store_config(dest) == {"base": "stub://host/base"}
    assert "s3cret" not in (dest / STORE_CONFIG).read_text(encoding="utf-8")
    assert "credentials" in caplog.text and "s3cret" not in caplog.text

    # The recorded base is what a later plain `init` composes over.
    assert _init(dest) == 0
    assert "# CUSTOM BASE" in (dest / "AGENTS.md").read_text(encoding="utf-8")


def test_an_unreachable_remote_is_a_one_line_error(tmp_path, monkeypatch, capsys):
    _stub_scheme(monkeypatch, tmp_path, [])
    with pytest.raises(SystemExit) as exc:
        _resolve_from("stub://bob:pw@host/missing", BASE_ROOT, cache_root=tmp_path / "c")
    message = str(exc.value)
    assert message.startswith("error: --from stub://host/missing") and "pw" not in message


needs_uri = pytest.mark.skipif(_sources.uri_path_class() is None, reason="needs pathlib_next[http]")


@needs_uri
def test_from_an_http_directory(tmp_path, monkeypatch):
    www = tmp_path / "www"
    _base(www / "base", "HTTP BASE")
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(www))
    handler.log_message = lambda *a: None
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    monkeypatch.setenv("AGENTS_HOME", str(tmp_path / "user"))
    try:
        url = "http://127.0.0.1:%d/base" % httpd.server_address[1]
        dest = tmp_path / "store"
        assert _init(dest, "--from", url) == 0
    finally:
        httpd.shutdown()
        httpd.server_close()
    assert "# HTTP BASE" in (dest / "AGENTS.md").read_text(encoding="utf-8")
    assert read_store_config(dest) == {"base": url}


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


def test_a_managed_block_merged_into_a_symlinked_file_keeps_the_link(tmp_path):
    """`~/.claude/CLAUDE.md` or Codex's config kept in a dotfiles repo. The
    block merge writes atomically now (a crash never leaves the file half
    written); this guards that the atomic path still writes the link's target
    rather than replacing the link, as a naive temp-and-rename would."""
    from dotagents import _merge

    real = tmp_path / "dotfiles" / "CLAUDE.md"
    real.parent.mkdir()
    real.write_text("# mine\n", encoding="utf-8")
    link = tmp_path / "CLAUDE.md"
    _symlink(real, link)
    block = "%s\nBASE\n%s\n" % (_merge.BEGIN_MARKER, _merge.END_MARKER)
    _merge.merge_block(link, block, force=False, dry_run=False)
    assert link.is_symlink()
    assert "# mine" in real.read_text(encoding="utf-8") and "BASE" in real.read_text(encoding="utf-8")
    assert [p.name for p in real.parent.iterdir()] == ["CLAUDE.md"], "no temp file left"


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
# --dry-run says "would"
# --------------------------------------------------------------------------- #


def test_dry_run_logs_what_would_happen(tmp_path, caplog):
    dest = Path.home() / ".agents"
    bin_dir = tmp_path / "mybin"
    with caplog.at_level(logging.INFO):
        assert _main([
            "init", "-g", "--agents", "claude,pi", "--no-hooks",
            "--dry-run", "--bin-dir", str(bin_dir), "--from", str(_base(tmp_path / "b")),
        ]) == 0
    messages = [r.getMessage() for r in caplog.records]
    assert "would create: AGENTS.md" in messages
    assert any(m.startswith("would create: ") and m.endswith("(include)") for m in messages)
    assert any(m.startswith("would create: ") and "(pointer to the store)" in m for m in messages)
    assert not any(m.startswith(("created:", "block-inserted:", "block-refreshed:")) for m in messages)
    for d in (dest / "bin", bin_dir):
        assert "would write wrappers: %s, %s" % (d / "dotagents", d / "dotagents.cmd") in messages
    assert "would record base: %s" % (dest / STORE_CONFIG) in messages
    assert not bin_dir.exists() and not any(Path.home().iterdir())


def test_dry_run_refresh_says_would_refresh(tmp_path, caplog):
    dest = tmp_path / "store"
    assert _init(dest) == 0
    text = (dest / "AGENTS.md").read_text(encoding="utf-8")
    (dest / "AGENTS.md").write_text(text.replace("## Load on demand", "## Load on demand\nSTALE"), encoding="utf-8")
    with caplog.at_level(logging.INFO):
        assert _init(dest, "--dry-run") == 0
    assert "would refresh the block: AGENTS.md" in [r.getMessage() for r in caplog.records]
    assert "STALE" in (dest / "AGENTS.md").read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# launch
# --------------------------------------------------------------------------- #

LAUNCH_PATH = ROOT / "src" / "dotagents" / "_overlay" / "dotagents" / "cmds" / "launch.py"


@pytest.fixture()
def launch_mod(monkeypatch, tmp_path):
    saved = dict(os.environ)
    store = tmp_path / "store"
    store.mkdir()
    monkeypatch.setenv("AGENTS_HOME", str(store))
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)
    spec = importlib.util.spec_from_file_location("test_fix_init_launch", LAUNCH_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    try:
        spec.loader.exec_module(mod)
        yield mod
    finally:
        sys.modules.pop(spec.name, None)
        os.environ.clear()
        os.environ.update(saved)


def _launch(mod, monkeypatch, **kwargs):
    calls = []
    monkeypatch.setattr(mod, "_spawn", lambda argv, env: calls.append((list(argv), dict(env))) or 0)
    cmd = mod.Launch()
    cmd._passthrough_ = []
    for k, v in kwargs.items():
        setattr(cmd, k, v)
    assert cmd() == 0
    (call,) = calls
    return call


@pytest.mark.skipif(os.name != "nt", reason="the implicit current-directory search is Windows-only")
@pytest.mark.parametrize("path_has_dot", [False, True])
def test_launch_never_runs_a_harness_planted_in_the_current_directory(
    launch_mod, monkeypatch, tmp_path, path_has_dot
):
    """Windows Python < 3.12 `shutil.which(path=...)` searched the cwd first,
    so a repo's own `claude.cmd` ran with the user's environment -- even
    under `-g`. A `.` PATH entry is the same hole, spelled out."""
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    real = fakebin / "claude.cmd"
    real.write_text("@echo REAL\r\n")
    (tmp_path / "project" / "claude.cmd").write_text("@echo PLANTED\r\n")
    (tmp_path / "project" / "claude.exe").write_bytes(b"MZ")
    path = os.pathsep.join([".", str(fakebin)] if path_has_dot else [str(fakebin)])
    monkeypatch.setenv("PATH", path)

    argv, _env = _launch(launch_mod, monkeypatch, agent="claude", no_context=True, global_scope=True)

    assert Path(argv[0]).resolve() == real.resolve()


def test_which_skips_relative_entries_and_honours_pathext(launch_mod, tmp_path, monkeypatch):
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    name = "tool.exe" if os.name == "nt" else "tool"
    exe = fakebin / name
    exe.write_bytes(b"MZ" if os.name == "nt" else b"#!/bin/sh\n")
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    found = launch_mod._which("tool", os.pathsep.join(["", str(fakebin)]), ".EXE")
    assert found is not None and Path(found).resolve() == exe.resolve()
    assert launch_mod._which("no-such-tool-xyz", str(fakebin), ".EXE") is None


def test_a_variable_an_env_layer_unset_does_not_reach_the_harness(launch_mod, monkeypatch, tmp_path):
    """`os.environ.update(changes)` ignored `changes.removed`, so the child
    still inherited a variable an env.py had unset (printed as null)."""
    (tmp_path / "store" / "env.py").write_text(
        "import json\nprint(json.dumps({'DROP_ME': None, 'KEEP': 'yes'}))\n", encoding="utf-8"
    )
    monkeypatch.setenv("DROP_ME", "secret")
    program = tmp_path / "bin" / ("h.cmd" if os.name == "nt" else "h")
    program.parent.mkdir()
    program.write_text("@echo off\r\n" if os.name == "nt" else "#!/bin/sh\n")
    program.chmod(program.stat().st_mode | stat.S_IXUSR)

    _argv, env = _launch(launch_mod, monkeypatch, agent="claude", command=str(program), no_context=True)

    assert env["KEEP"] == "yes"
    assert "DROP_ME" not in env and "DROP_ME" not in os.environ


def test_launch_dry_run_names_the_unset_variables(launch_mod, monkeypatch, tmp_path, capsys):
    (tmp_path / "store" / "env.py").write_text(
        "import json\nprint(json.dumps({'DROP_ME': None}))\n", encoding="utf-8"
    )
    monkeypatch.setenv("DROP_ME", "secret")
    cmd = launch_mod.Launch()
    cmd._passthrough_ = []
    cmd.agent, cmd.command, cmd.no_context, cmd.dry_run = "claude", "no-such-xyz", True, True
    assert cmd() == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[2] == "unset: DROP_ME"


# --------------------------------------------------------------------------- #
# overlays: the setup helper that set AGENTS_HOME to the scope store is gone
# --------------------------------------------------------------------------- #


def test_the_old_overlay_setup_helper_is_gone():
    """`_run_overlay_setup` passed the scope store as `AGENTS_HOME` and had no
    caller left; `Overlay.run_setup(scope_root=, scope_level=)` replaced it."""
    from dotagents.cli import _common

    assert not hasattr(cli, "_run_overlay_setup")
    assert not hasattr(_common, "_run_overlay_setup")
