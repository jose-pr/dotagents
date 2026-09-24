"""The CLI as a user drives it: `dotagents.cli.main(argv)`, so argv goes
through duho's flag declarations instead of attributes set on a command
object. Covers each command's --help (built-ins, the bundled modules and the
nested `findings` subcommands), `init`/`env` round-trips, `--from`,
`--force`, re-running `init` after `overlays add`, and the argv helpers that
run before any parser exists.

tests/conftest.py isolates every test (tmp home, empty cwd, no session vars).
"""

import json
import os
from pathlib import Path

import pytest

from dotagents import cli
from dotagents._wrappers import check_path_warning


def _main(argv):
    """`main` returns an rc, or raises SystemExit (usage errors, --help);
    either way, the rc."""
    try:
        return cli.main(list(argv))
    except SystemExit as exc:
        if exc.code is None:
            return 0
        return exc.code if isinstance(exc.code, int) else 2


# --------------------------------------------------------------------------
# --help: every command's declared flags reach the parser
# --------------------------------------------------------------------------

_SCOPED = ["--global", "--agents-dir"]

HELP = {
    ("init",): _SCOPED + ["--dest", "--from", "--bin-dir", "--dry-run", "--force", "--agents", "--no-hooks"],
    ("env",): _SCOPED + ["--format", "--diff"],
    ("context",): _SCOPED + ["--format", "--agents", "--write-agent", "--inline"],
    ("about",): ["--json"],
    ("build-pyz",): ["--out", "--python", "--duho-version", "--pathlib-next-version", "--extras"],
    ("overlays", "add"): _SCOPED + ["--repo", "--copy", "--no-setup", "--no-requires", "--dry-run"],
    ("overlays", "sync"): _SCOPED + ["--repo", "--copy", "--overwrite", "--no-setup", "--dry-run"],
    ("overlays", "list"): _SCOPED + ["--repo", "--json"],
    ("overlays", "remove"): _SCOPED + ["--dry-run"],
    ("overlays", "show"): _SCOPED + ["--repo", "--json"],
    ("launch",): _SCOPED + ["--command", "--no-context", "--inline", "--write-agent", "--dry-run"],
    ("findings", "add"): _SCOPED + ["--dir", "--name", "--body", "--body-file"],
    ("findings", "list"): _SCOPED + ["--dir", "--all", "--processed", "--json"],
    ("findings", "show"): _SCOPED + ["--dir"],
    ("findings", "done"): _SCOPED + ["--dir", "--resolution", "--resolution-file"],
    ("findings", "reopen"): _SCOPED + ["--dir"],
    ("findings", "remove"): _SCOPED + ["--dir"],
    ("findings", "index"): _SCOPED + ["--dir"],
    ("findings", "path"): _SCOPED + ["--dir"],
}


@pytest.mark.parametrize("argv", sorted(HELP), ids=" ".join)
def test_help_lists_the_declared_flags(argv, capsys):
    assert _main([*argv, "--help"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("usage: dotagents " + " ".join(argv)), out[:200]
    missing = [flag for flag in HELP[argv] if flag not in out]
    assert not missing, "%s --help lacks %s" % (" ".join(argv), missing)


# --------------------------------------------------------------------------
# init
# --------------------------------------------------------------------------

def test_init_round_trip_with_two_agents_and_no_hooks(tmp_path):
    project = tmp_path / "proj"
    dest = project / ".agents"
    assert _main(["init", "--dest", str(dest), "--agents", "claude,codex", "--no-hooks"]) == 0
    assert "<!-- dotagents:begin -->" in (dest / "AGENTS.md").read_text(encoding="utf-8")
    include = project / ".claude" / "CLAUDE.md"  # claude's half; codex reads AGENTS.md itself
    assert "@../.agents/AGENTS.md" in include.read_text(encoding="utf-8")
    assert (dest / "bin" / "dotagents").is_file() and (dest / "bin" / "dotagents.cmd").is_file()
    # --no-hooks: neither adapter wired anything.
    assert not (project / ".claude" / "settings.local.json").exists()
    assert not (Path.home() / ".codex").exists()
    assert not (Path.home() / ".claude").exists()


def test_init_from_a_missing_base_is_a_usage_error(tmp_path, capsys):
    rc = _main(["init", "--dest", str(tmp_path / "s"), "--from", str(tmp_path / "missing"), "--no-hooks"])
    assert rc != 0
    assert not (tmp_path / "s" / "AGENTS.md").exists()


def test_init_force_backs_up_the_original(tmp_path):
    dest = tmp_path / "store"
    dest.mkdir()
    (dest / "AGENTS.md").write_text("MY OWN AGENTS.md\n", encoding="utf-8")
    assert _main(["init", "--dest", str(dest), "--agents", "codex", "--no-hooks", "--force"]) == 0
    assert "MY OWN" not in (dest / "AGENTS.md").read_text(encoding="utf-8")
    backups = list((dest / "install_backup").rglob("AGENTS.md"))
    assert [p.read_text(encoding="utf-8") for p in backups] == ["MY OWN AGENTS.md\n"]


def test_reinit_after_overlays_add_keeps_the_overlay_routing(tmp_path):
    store = tmp_path / "store"
    src = tmp_path / "src" / "tiny"
    src.mkdir(parents=True)
    (src / "overlay.toml").write_text(
        'name = "tiny"\nrouting = ["- Tiny -> $TINY_OVERLAY_ROOT/kb/T.md"]\n', encoding="utf-8"
    )
    init = ["init", "--dest", str(store), "--agents", "codex", "--no-hooks"]
    assert _main(init) == 0
    assert _main(["overlays", "add", "tiny", "--repo", str(src.parent), "-g",
                  "--agents-dir", str(store), "--copy"]) == 0
    after_add = (store / "AGENTS.md").read_text(encoding="utf-8")
    assert "TINY_OVERLAY_ROOT" in after_add
    assert _main(init) == 0
    assert (store / "AGENTS.md").read_text(encoding="utf-8") == after_add


@pytest.mark.xfail(strict=True, reason="open (review 2026-09-23 adapters-06): an unknown "
                   "--agents name is a warning and init exits 0 having configured nothing")
def test_init_with_an_unknown_agent_is_a_usage_error(tmp_path):
    assert _main(["init", "--dest", str(tmp_path / "s"), "--agents", "claud", "--no-hooks"]) == 2


# --------------------------------------------------------------------------
# env --diff: the SessionStart hook's own call
# --------------------------------------------------------------------------

def test_env_diff_json_holds_only_what_changed(tmp_path, monkeypatch, capsys):
    store = tmp_path / "store"
    store.mkdir()
    (store / "env.py").write_text(
        "import json\nprint(json.dumps({'FROM_STORE': 'yes', 'HOME': %r}))\n" % os.environ["HOME"],
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENTS_HOME", str(store))
    assert _main(["env", "--diff", "--format", "json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["FROM_STORE"] == "yes"
    assert "HOME" not in out, "set to the value it already had: not a change"
    assert "AGENTS_HOME" not in out, "already exported with the value env resolved"
    assert Path(out["AGENTS_PROJECT_ROOT"]) == Path.cwd(), "unset before: part of the diff"


def test_env_global_with_agents_dir_uses_that_store_and_no_project(tmp_path, capsys):
    """`-g --agents-dir X`: the store is X, and `-g` drops the project tier
    (here: the cwd's `.agents`)."""
    store = tmp_path / "x-store"
    store.mkdir()
    (store / "env.py").write_text("import json\nprint(json.dumps({'FROM_X': '1'}))\n", encoding="utf-8")
    project = Path.cwd() / ".agents"
    project.mkdir()
    (project / "env.py").write_text("import json\nprint(json.dumps({'FROM_PROJECT': '1'}))\n", encoding="utf-8")
    assert _main(["env", "-g", "--agents-dir", str(store), "--format", "json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["FROM_X"] == "1" and "FROM_PROJECT" not in out


# --------------------------------------------------------------------------
# argv helpers that run before any parser exists
# --------------------------------------------------------------------------

@pytest.mark.parametrize("argv, expected", [
    (["env", "--agents-dir", "X"], "X"),
    (["overlays", "list", "--agents-dir=Y", "-g"], "Y"),
    (["env", "--agents-dir"], None),
    (["env"], None),
])
def test_agents_dir_is_read_from_raw_argv(argv, expected):
    assert cli._agents_dir_from_argv(argv) == expected


def test_discovery_walks_the_store_named_on_the_command_line(tmp_path):
    store = tmp_path / "other-store"
    cmds = store / "dotagents" / "cmds"
    cmds.mkdir(parents=True)
    (cmds / "toy.py").write_text(
        "from duho import Cmd, LoggingArgs\n\n\n"
        "class Toy(LoggingArgs, Cmd):\n    _parsername_ = 'toy'\n\n"
        "    def __call__(self):\n        return 0\n",
        encoding="utf-8",
    )
    names = {getattr(c, "_parsername_", None) or c.__name__ for c in cli._discover(["env", "--agents-dir", str(store)])}
    assert "toy" in names
    names = {getattr(c, "_parsername_", None) or c.__name__ for c in cli._discover(["env"])}
    assert "toy" not in names


def test_check_path_warning(tmp_path, monkeypatch):
    on, off = tmp_path / "on", tmp_path / "off"
    on.mkdir()
    off.mkdir()
    monkeypatch.setenv("PATH", str(on) + os.pathsep + os.environ.get("PATH", ""))
    assert check_path_warning(on) is None
    warning = check_path_warning(off)
    assert warning and str(off.resolve()) in warning and "PATH" in warning
