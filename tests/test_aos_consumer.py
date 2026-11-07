"""dotagents as an AOS consumer: what an overlay declares is applied, and only
that; pins, grants and the env-script opt-in come from the store's own
configuration; text values are substituted; setup is told its phase and
writes into a state directory; weakenings
are reported; what this machine lacks refuses an install before it starts.

tmp dirs only; the conftest isolates the home and the stores.
"""

import json
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

import coqui_aos
import pytest

from dotagents import _aos, _env, _skills
from dotagents._overlays import Overlay
from dotagents._scope import Scope
from dotagents.cli import OverlayAdd, OverlayShow, OverlaySync

from _helpers import run_cmd as _run

BASE_AGENTS = "<!-- dotagents:begin -->\n# BASE\n## Load on demand\n<!-- dotagents:end -->\n"
GIT = shutil.which("git")


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(str(path), "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    return path


def _logger():
    return logging.getLogger("test-aos-consumer")


@pytest.fixture
def store(tmp_path):
    store = tmp_path / "store"
    _write(store / "AGENTS.md", BASE_AGENTS)
    return store


def _env_of(store, base=None):
    return _env.get_environment(Scope("user", store), base_env=dict(base or {"PATH": ""}), logger=_logger())


def _add(src, store, *names, **extra):
    return _run(OverlayAdd, name=list(names), repo=[str(src)], global_scope=True, agents_dir=store,
                copy=True, dry_run=False, **extra)


# --------------------------------------------------------------------------
# The environment: env, env_files, paths, secret references and grants
# --------------------------------------------------------------------------

def test_env_env_files_and_paths_apply_in_order(store):
    ov = store / "overlays" / "docs"
    _write(ov / "overlay.cfg",
           'env_files = ["env/defaults.cfg", { path = "env/banner.env", format = "dotenv" }]\n'
           '[env]\nMODE = "from-env"\nCONF = { path = "conf/tool.toml" }\n'
           'KEEP = { value = "overlay", if_unset = true }\n'
           '[[paths]]\nvariable = "PATH"\npath = "bin"\n'
           '[[paths]]\nvariable = "DOCS_PATH"\npath = "share"\nposition = "append"\n')
    _write(ov / "env" / "defaults.cfg", 'MODE = "from-file"\nFROM_FILE = "1"\n')
    _write(ov / "env" / "banner.env", "# comment\nexport BANNER='read only'\n")
    env = _env_of(store, {"PATH": "", "KEEP": "user", "DOCS_PATH": "/x"})
    sep = os.pathsep
    root = str(ov)
    assert env["MODE"] == "from-env", "env applies after the env files"
    assert env["FROM_FILE"] == "1" and env["BANNER"] == "read only"
    assert env["CONF"] == os.path.join(root, "conf", "tool.toml")
    assert "KEEP" not in env, "if_unset keeps the inherited value"
    assert os.path.join(root, "bin") in env["PATH"].split(sep)
    assert env["DOCS_PATH"] == sep.join(["/x", os.path.join(root, "share")])


def test_bin_and_lib_are_path_additions_by_default(store):
    """No manifest needed for the basics: an existing bin/ is prepended to
    PATH, an existing lib/ appended to PYTHONPATH; `[defaults]` turns either off."""
    plain = store / "overlays" / "plain"
    _write(plain / "bin" / "tool", "#!/bin/sh\n")
    _write(plain / "lib" / "python" / "plain_mod.py", "")
    quiet = store / "overlays" / "quiet"
    _write(quiet / "bin" / "tool", "#!/bin/sh\n")
    _write(quiet / "lib" / "python" / "quiet_mod.py", "")
    _write(quiet / "overlay.cfg", "[defaults]\nbin = false\nlib = false\n")
    bare = store / "overlays" / "bare"
    (bare / "bin").mkdir(parents=True)  # empty: not present
    _write(bare / "lib" / "bare_mod.py", "")  # a bare lib/ means nothing
    env = _env_of(store, {"PATH": "/usr/bin", "PYTHONPATH": "/site"})
    path, pythonpath = env["PATH"].split(os.pathsep), env["PYTHONPATH"].split(os.pathsep)
    assert path.index(str(plain / "bin")) < path.index("/usr/bin")
    assert pythonpath == [str(plain / "lib" / "python"), "/site"]
    assert str(quiet / "bin") not in path and str(quiet / "lib" / "python") not in pythonpath
    assert not any("bare" in entry for entry in path + pythonpath)
    assert [issue.at for issue in coqui_aos.validate({"defaults": {"bin": "no"}})] == ["/defaults/bin"]
    assert [issue.at for issue in coqui_aos.validate({"defaults": {"libs": False}})] == ["/defaults/libs"]


def test_a_declared_entry_naming_a_default_replaces_it(store):
    ov = store / "overlays" / "decl"
    _write(ov / "bin" / "tool", "#!/bin/sh\n")
    _write(ov / "lib" / "python" / "decl_mod.py", "")
    _write(ov / "man" / "tool.1", "")
    _write(ov / "overlay.cfg", '[[paths]]\nvariable = "PYTHONPATH"\npath = "lib/python"\nposition = "append"\n'
                               '[[paths]]\nvariable = "MANPATH"\npath = "man"\n')
    env = _env_of(store, {"PATH": "/usr/bin", "PYTHONPATH": "/site"})
    assert str(ov / "bin") in env["PATH"].split(os.pathsep), "declaring another directory keeps bin/"
    assert env["PYTHONPATH"].split(os.pathsep) == ["/site", str(ov / "lib" / "python")]
    assert env["MANPATH"] == str(ov / "man")


def test_a_reference_needs_a_grant_and_is_reported(store, caplog):
    ov = store / "overlays" / "docs"
    _write(ov / "overlay.cfg", '[env]\nTOKEN = { provider = "env", key = "HOST_TOKEN" }\nOTHER = "x"\n')
    with caplog.at_level(logging.WARNING):
        env = _env_of(store, {"PATH": "", "HOST_TOKEN": "s3cret"})
    assert "TOKEN" not in env and "OTHER" not in env, "nothing of the overlay applies"
    assert "unresolved-reference TOKEN" in caplog.text and "s3cret" not in caplog.text
    _write(store / "dotagents.toml", '[overlays.docs]\ngrants = { env = ["HOST_TOKEN"] }\n')
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        env = _env_of(store, {"PATH": "", "HOST_TOKEN": "s3cret"})
    assert env["TOKEN"] == "s3cret" and env["OTHER"] == "x"
    assert "env-passthrough HOST_TOKEN" in caplog.text and "s3cret" not in caplog.text


def test_named_and_file_secrets(store, tmp_path):
    secrets = tmp_path / "secrets"
    _write(secrets / "docs" / "token", "from-file\n")
    _write(store / "dotagents.toml",
           'secrets_dir = %s\n[secrets.signing]\nprovider = "env"\nkey = "SIGNING"\n'
           '[overlays.docs]\ngrants = { secret = ["signing"], file = ["docs/token"] }\n'
           % json.dumps(str(secrets)))
    _write(store / "overlays" / "docs" / "overlay.cfg",
           '[env]\nA = { secret = "signing" }\nB = { provider = "file", key = "docs/token" }\n'
           'C = { secret = "absent", optional = true }\n')
    env = _env_of(store, {"PATH": "", "SIGNING": "named"})
    assert env["A"] == "named" and env["B"] == "from-file" and "C" not in env


def test_an_overlay_cannot_set_another_overlays_root(store):
    _write(store / "overlays" / "evil" / "env" / "x.env", "TARGET_OVERLAY_ROOT=/elsewhere\n")
    _write(store / "overlays" / "evil" / "overlay.cfg", 'env_files = [{ path = "env/x.env", format = "dotenv" }]\n')
    _write(store / "overlays" / "target" / "kb" / "T.md", "t\n")
    env = _env_of(store)
    assert env["TARGET_OVERLAY_ROOT"] == str(store / "overlays" / "target")


def test_unpinned_and_sensitive_weakenings_are_reported(store, caplog):
    _write(store / "overlays" / "tool" / "overlay.cfg", '[env]\nNODE_OPTIONS = "--x"\n')
    with caplog.at_level(logging.WARNING):
        _env_of(store)
    assert "tool[unpinned, env-sensitive NODE_OPTIONS]" in caplog.text


# --------------------------------------------------------------------------
# Substitution: ${NAME} in text values, chained in the order of application
# --------------------------------------------------------------------------

def test_values_are_substituted_in_the_order_of_application(store):
    """A dotenv file chains line by line and names its own root; a later
    overlay reads what an earlier one set; one mapping's entries see the
    environment from before it; single quotes keep a value as written."""
    low, high = store / "overlays" / "low", store / "overlays" / "high"
    _write(low / "overlay.cfg", "priority = 100\n")
    _write(low / "env", "TOOL_HOME=${AOS_OVERLAY_ROOT}/tool\nTOOL_CONF=${TOOL_HOME}/conf\n"
                        "PROMPT='${USER}$ '\nPRICE=$$5 ${MISSING-none}\n")
    _write(high / "overlay.cfg", 'priority = 900\n[env]\nTOOL_BIN = "${TOOL_HOME}/bin"\n'
                                 'LOW = "${LOW_OVERLAY_ROOT}"\nNAME = "${AOS_OVERLAY_NAME^^}"\n')
    env = _env_of(store)
    tool = str(low) + "/tool"
    assert env["TOOL_HOME"] == tool and env["TOOL_CONF"] == tool + "/conf"
    assert env["PROMPT"] == "${USER}$ " and env["PRICE"] == "$5 none"
    assert env["TOOL_BIN"] == tool + "/bin" and env["LOW"] == str(low) and env["NAME"] == "HIGH"


def test_an_undefined_name_refuses_the_overlays_environment(store, caplog):
    ov = store / "overlays" / "docs"
    _write(ov / "overlay.cfg", '[env]\nA = "x"\nB = "${A}/y"\nC = "kept"\n')
    with caplog.at_level(logging.ERROR):
        env = _env_of(store)
    assert not {"A", "B", "C"} & set(env), "nothing of the overlay applies"
    assert "undefined-variable B" in caplog.text, "the entries of one mapping do not see each other"


def test_no_placeholder_reads_a_secret(store, caplog):
    _write(store / "dotagents.toml", '[overlays.low]\ngrants = { env = ["HOST_TOKEN"] }\n')
    _write(store / "overlays" / "low" / "overlay.cfg",
           'priority = 100\n[env]\nTOKEN = { provider = "env", key = "HOST_TOKEN" }\n')
    _write(store / "overlays" / "high" / "overlay.cfg",
           'priority = 900\n[env]\nAUTH = "Bearer ${TOKEN}"\nLEN = "${#TOKEN}"\n')
    with caplog.at_level(logging.ERROR):
        env = _env_of(store, {"PATH": "", "HOST_TOKEN": "s3cret"})
    assert env["TOKEN"] == "s3cret" and "AUTH" not in env and "LEN" not in env
    assert "secret-placeholder" in caplog.text and "s3cret" not in caplog.text


def test_an_env_script_sees_the_value_as_substituted_for_the_session(store):
    """An overlay's own processes get its values as they were substituted in
    the order of application, never substituted again over another base."""
    _write(store / "overlays" / "low" / "overlay.cfg", 'priority = 100\n[env]\nTOOL_HOME = "/opt/tool"\n')
    ov = store / "overlays" / "high"
    _write(ov / "overlay.cfg", 'priority = 900\n[env]\nTOOL_BIN = "${TOOL_HOME}/bin"\n')
    _write(ov / "env.py", "import json, os\nprint(json.dumps({'SEEN': os.environ['TOOL_BIN'],"
                          " 'HOME_SEEN': os.environ.get('TOOL_HOME', '')}))\n")
    env = _env_of(store)
    assert env["SEEN"] == "/opt/tool/bin"
    assert env["HOME_SEEN"] == "", "another overlay's own values stay out of its processes"


def test_an_mcp_servers_env_is_substituted_against_what_it_receives(tmp_path):
    from dotagents._mcp_gateway_config import _ServerSpec

    spec = _ServerSpec("docs", {"name": "s", "transport": "stdio", "command": "x", "env": {
        "HOME_DIR": "${AOS_OVERLAY_ROOT}/home", "NAME": "${AOS_OVERLAY_NAME}",
        "A": "a", "B": {"value": "${A:-unset}"}}})
    spec.root = tmp_path
    assert spec.server_env({"PATH": "/bin"}) == {
        "HOME_DIR": str(tmp_path) + "/home", "NAME": "docs", "A": "a", "B": "unset"}
    with pytest.raises(RuntimeError, match="undefined-variable"):
        _ServerSpec("docs", {"name": "s", "transport": "stdio", "command": "x",
                             "env": {"X": "${NOPE}"}}).server_env({})
    with pytest.raises(RuntimeError, match="secret-placeholder"):
        _ServerSpec("docs", {"name": "s", "transport": "stdio", "command": "x",
                             "env": {"X": "${PASSED}"}}).server_env({"PASSED": "v"}, ["PASSED"])


# --------------------------------------------------------------------------
# Env scripts: declared or the default env.<ext>; the store can turn them off
# --------------------------------------------------------------------------

def test_env_scripts_run_and_are_reported_unless_the_store_turns_them_off(store, caplog):
    ov = store / "overlays" / "calc"
    _write(ov / "overlay.cfg", '[[env_scripts]]\ninterpreter = "python"\npath = "env/compute.py"\ntimeout = 30\n')
    _write(ov / "env" / "compute.py",
           "import json, os\nprint(json.dumps({'COMPUTED': os.environ['AOS_OVERLAY_NAME']}))\n"
           "print(json.dumps({'GONE': None}))\n")
    with caplog.at_level(logging.WARNING):
        env = _env_of(store, {"PATH": "", "GONE": "x"})
    assert env["COMPUTED"] == "calc" and "GONE" in env.removed
    assert "env-script env/compute.py" in caplog.text
    _write(store / "dotagents.toml", '[overlays.calc]\nenv_scripts = false\n')
    env = _env_of(store, {"PATH": "", "GONE": "x"})
    assert "COMPUTED" not in env and "GONE" not in env.removed


def test_the_default_env_file_and_env_script(store, caplog):
    """An overlay's root `env` is a dotenv env file and its first runnable
    `env.<ext>` an env script, without a manifest; `[defaults] env = false`
    turns both off. `pre.env*` and `local.env` in an overlay mean nothing."""
    ov = store / "overlays" / "plain"
    _write(ov / "env", "FROM_DOTENV='literal $HOME ${HOME}'\n")
    _write(ov / "env.py", "import json\nprint(json.dumps({'FROM_SCRIPT': '1'}))\n")
    _write(ov / "env.sh", "echo never\n")
    _write(ov / "pre.env.py", "import json\nprint(json.dumps({'FROM_PRE': '1'}))\n")
    _write(ov / "local.env", "FROM_LOCAL=1\n")
    with caplog.at_level(logging.WARNING):
        env = _env_of(store)
    assert env["FROM_DOTENV"] == "literal $HOME ${HOME}" and env["FROM_SCRIPT"] == "1"
    assert "FROM_PRE" not in env and "FROM_LOCAL" not in env
    assert "plain[unpinned, env-script env.py]" in caplog.text
    _write(ov / "overlay.cfg", "[defaults]\nenv = false\n")
    env = _env_of(store)
    assert "FROM_DOTENV" not in env and env["FROM_SCRIPT"] == "1", "env turns off only the dotenv file"
    _write(ov / "overlay.cfg", "[defaults]\nenv_scripts = false\n")
    env = _env_of(store)
    assert env["FROM_DOTENV"] == "literal $HOME ${HOME}" and "FROM_SCRIPT" not in env


def test_the_default_script_order_and_a_configured_one(store, caplog):
    """`.py`, `.js`, `.mjs`, `.cjs` on every OS, `.ps1` on Windows only, then
    `.bash`, `.sh` elsewhere; a declared entry naming one keeps its place
    with its own settings, and a pretty-printed object is accepted."""
    ov = store / "overlays" / "order"
    shell = "env.ps1" if os.name == "nt" else "env.sh"
    _write(ov / shell, "unused\n")
    _write(ov / "env.py", "import json\nprint(json.dumps({'WHICH': 'py', 'ARGS': ','.join(__import__('sys').argv[1:])}, indent=2))\n")
    env = _env_of(store)
    assert env["WHICH"] == "py" and env["ARGS"] == ""
    _write(ov / "overlay.cfg", '[[env_scripts]]\ninterpreter = "python"\npath = "env.py"\nargs = ["a", "b"]\n')
    env = _env_of(store)
    assert env["WHICH"] == "py" and env["ARGS"] == "a,b"
    (ov / "env.py").unlink()
    _write(ov / "overlay.cfg", "")
    with caplog.at_level(logging.WARNING):
        env = _env_of(store)
    assert "WHICH" not in env
    if os.name == "nt" and shutil.which("pwsh") is None:
        assert "script-not-run env.ps1" in caplog.text


def test_a_store_pre_file_runs_before_the_overlays_and_its_env_after(store):
    _write(store / "pre.env.py", "import json\nprint(json.dumps({'SEED': 'store-pre', 'WHO': 'pre'}))\n")
    _write(store / "overlays" / "plain" / "env.py",
           "import json, os\nprint(json.dumps({'SAW_SEED': os.environ.get('SEED', '-'), 'WHO': 'overlay'}))\n")
    _write(store / "env.py", "import json, os\nprint(json.dumps({'LAST': os.environ['WHO']}))\n")
    env = _env_of(store)
    assert env["SAW_SEED"] == "store-pre" and env["LAST"] == "overlay"


def test_an_env_script_cannot_set_a_reserved_variable(store, caplog):
    ov = store / "overlays" / "calc"
    _write(ov / "overlay.cfg", 'env_scripts = [{ interpreter = "python", path = "e.py" }]\n')
    _write(ov / "e.py", "import json\nprint(json.dumps({'CALC_OVERLAY_ROOT': '/x', 'OK': '1'}))\n")
    with caplog.at_level(logging.ERROR):
        env = _env_of(store)
    assert env["CALC_OVERLAY_ROOT"] == str(ov) and "OK" not in env
    assert "env script e.py failed" in caplog.text


# --------------------------------------------------------------------------
# Pins
# --------------------------------------------------------------------------

def _source(tmp_path, name="docs", text="# docs\n"):
    src = tmp_path / "src"
    _write(src / name / "kb" / "D.md", text)
    _write(src / name / "overlay.cfg", 'routing = ["- docs -> $DOCS_OVERLAY_ROOT/kb/D.md"]\n')
    return src


def test_a_pinned_overlay_installs_only_with_its_digest(tmp_path, store):
    src = _source(tmp_path)
    digest = _aos.tree_digest(src / "docs")
    _write(store / "dotagents.toml", '[overlays.docs]\npin = "sha256:%s"\n' % ("0" * 64))
    with pytest.raises(SystemExit, match="is not its pin"):
        _add(src, store, "docs")
    assert not (store / "overlays" / "docs").exists(), "refused before anything is written"
    _write(store / "dotagents.toml", '[overlays.docs]\npin = "%s"\n' % digest.upper().replace("SHA256", "sha256"))
    assert _add(src, store, "docs") == 0
    assert Overlay(store / "overlays" / "docs").read_install_record()["digest"] == digest


def test_a_pinned_copy_that_changed_is_refused_at_activation(tmp_path, store, caplog):
    src = _source(tmp_path)
    _write(store / "dotagents.toml", '[overlays.docs]\npin = "%s"\n' % _aos.tree_digest(src / "docs"))
    assert _add(src, store, "docs") == 0
    assert _env_of(store)["DOCS_OVERLAY_ROOT"]
    _write(store / "overlays" / "docs" / "kb" / "D.md", "tampered\n")
    _write(store / "overlays" / ".dotagents-install" / "docs.json",
           (store / "overlays" / ".dotagents-install" / "docs.json").read_text(encoding="utf-8") + " ")
    with caplog.at_level(logging.WARNING):
        env = _env_of(store)
    assert "DOCS_OVERLAY_ROOT" not in env
    assert "overlay docs is refused: its tree digest" in caplog.text
    assert Scope("user", store).overlays == []


def test_a_pinned_copy_holding_a_cache_is_refused(tmp_path, store, caplog):
    src = _source(tmp_path)
    _write(store / "dotagents.toml", '[overlays.docs]\npin = "%s"\n' % _aos.tree_digest(src / "docs"))
    assert _add(src, store, "docs") == 0
    _write(store / "overlays" / "docs" / "__pycache__" / "x.pyc", "x")
    _write(store / "dotagents.toml", '[overlays.docs]\npin = "%s"\n\n' % _aos.tree_digest(src / "docs"))
    with caplog.at_level(logging.WARNING):
        assert Scope("user", store).overlays == []
    assert "__pycache__" in caplog.text


def test_show_reports_digest_pin_and_weakenings(tmp_path, store, capsys):
    src = _source(tmp_path)
    assert _add(src, store, "docs") == 0
    capsys.readouterr()
    _run(OverlayShow, name="docs", repo=[str(src)], global_scope=True, agents_dir=store, json=True)
    info = json.loads(capsys.readouterr().out)
    assert info["digest"] == _aos.tree_digest(src / "docs") and info["pin"] is None
    assert ["unpinned", ""] in info["weakenings"]


# --------------------------------------------------------------------------
# Setup in phases, state outside the root
# --------------------------------------------------------------------------

def test_setup_writes_state_and_the_session_names_it(tmp_path, store):
    src = tmp_path / "src"
    _write(src / "tool" / "overlay.cfg",
           '[[setup]]\ninterpreter = "python"\npath = "setup/setup.py"\nargs = ["a b", "$HOME"]\n')
    _write(src / "tool" / "setup" / "setup.py",
           "import json, os, sys\n"
           "state = os.environ['AOS_OVERLAY_STATE']\n"
           "open(os.path.join(state, 'built.json'), 'w').write(json.dumps({\n"
           " 'args': sys.argv[1:], 'root': os.environ['AOS_OVERLAY_ROOT'],\n"
           " 'name': os.environ['AOS_OVERLAY_NAME'], 'var': os.environ['TOOL_OVERLAY_STATE']}))\n")
    assert _add(src, store, "tool") == 0
    installed = Overlay(store / "overlays" / "tool")
    state = installed.state_dir()
    built = json.loads((state / "built.json").read_text(encoding="utf-8"))
    assert built == {"args": ["install", "a b", "$HOME"], "root": str(installed.path), "name": "tool", "var": str(state)}
    assert installed.path not in state.parents, "the state directory is outside the root"
    assert installed.tree_digest() == _aos.tree_digest(src / "tool"), "setup never wrote into the root"
    assert _env_of(store)["TOOL_OVERLAY_STATE"] == str(state)


def test_setup_with_network_is_reported(tmp_path, store, caplog):
    src = tmp_path / "src"
    _write(src / "tool" / "overlay.cfg", '[[setup]]\ninterpreter = "python"\npath = "s.py"\nnetwork = true\n')
    _write(src / "tool" / "s.py", "")
    with caplog.at_level(logging.WARNING):
        assert _add(src, store, "tool") == 0
    assert "setup-network s.py" in caplog.text


def test_a_setup_variant_for_another_os_does_not_run(tmp_path, store):
    other = "linux" if _aos.current_os() == "windows" else "windows"
    src = tmp_path / "src"
    _write(src / "tool" / "overlay.cfg",
           '[[setup]]\nos = ["%s"]\ninterpreter = "python"\npath = "s.py"\n' % other)
    _write(src / "tool" / "s.py", "open(%r, 'w').write('ran')\n" % str(tmp_path / "RAN"))
    assert _add(src, store, "tool") == 0
    assert not (tmp_path / "RAN").exists()


# --------------------------------------------------------------------------
# Runtime probing refuses before anything is written
# --------------------------------------------------------------------------

@pytest.mark.parametrize("manifest, why", [
    ('[runtime]\nos = ["haiku"]\n', "does not support"),
    ('[runtime]\ncommands = ["no-such-command-aos"]\n', "no-such-command-aos is not on PATH"),
    ('[[runtime.python]]\nversion = "99"\n', "python 99 or later"),
    ('[[setup]]\ninterpreter = "cobol"\npath = "s.cob"\n', "interpreter 'cobol' is not supported"),
    ('[scripts]\nlint = { path = "lint.rb", interpreter = "ruby" }\n', "ruby"),
])
def test_what_this_machine_lacks_refuses_the_install(tmp_path, store, manifest, why):
    src = tmp_path / "src"
    _write(src / "needy" / "overlay.cfg", manifest)
    for rel in ("s.cob", "lint.rb"):
        _write(src / "needy" / rel, "")
    with pytest.raises(SystemExit, match=why):
        _add(src, store, "needy")
    assert not (store / "overlays" / "needy").exists()


def test_a_named_file_that_is_missing_refuses_the_install(tmp_path, store):
    src = tmp_path / "src"
    _write(src / "broken" / "overlay.cfg", 'rules = ["rules/NOPE.md"]\n')
    with pytest.raises(SystemExit, match="missing-file at /rules/0"):
        _add(src, store, "broken")


def test_lock_files_are_checked_before_pip(tmp_path):
    from dotagents import _pyvenv

    lock = _write(tmp_path / "lock.txt", "tomli==2.0.1 \\\n    --hash=sha256:%s\n" % ("a" * 64))
    assert _pyvenv.check_dependencies(lock, True, None) == []
    assert "lock_digest" in _pyvenv.check_dependencies(lock, True, "sha256:" + "0" * 64)[0]
    assert _pyvenv.check_dependencies(lock, True, _aos.sha256_digest(lock.read_bytes())) == []
    loose = _write(tmp_path / "req.txt", "-r other.txt\nrequests>=2\n--index-url https://x\n")
    assert len(_pyvenv.check_dependencies(loose, False, None)) == 2
    unpinned = _write(tmp_path / "unpinned.txt", "requests>=2 --hash=sha256:%s\n" % ("b" * 64))
    assert any("not pinned" in p for p in _pyvenv.check_dependencies(unpinned, True, None))


# --------------------------------------------------------------------------
# Harness hints
# --------------------------------------------------------------------------

def test_a_harness_hint_keeps_skills_from_that_harness(tmp_path):
    store = tmp_path / "store"
    _write(store / "overlays" / "docs" / "overlay.cfg", '[harness.claude-code]\nskills = false\n')
    _write(store / "overlays" / "docs" / "skills" / "search" / "SKILL.md", "---\nname: search\n---\n")
    _write(store / "overlays" / "other" / "skills" / "keep" / "SKILL.md", "---\nname: keep\n---\n")
    assert _skills.harness_excluded(store, "claude-code") == {"search"}
    assert _skills.harness_excluded(store, "codex") == set()
    shared = store / "skills"
    for name in ("search", "keep"):
        _write(shared / name / "SKILL.md", "x\n")
    result = _skills.link_skills_into(shared, tmp_path / "claude-skills", exclude={"search"})
    assert sorted(result.owned) == ["keep"]


# --------------------------------------------------------------------------
# Git sources: the bytes are the blobs', whatever the checkout settings
# --------------------------------------------------------------------------

@pytest.mark.skipif(GIT is None, reason="needs git on PATH")
def test_a_git_source_installs_the_blob_bytes_and_records_its_commit(tmp_path, store, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    work = tmp_path / "work"

    def git(*args):
        subprocess.run([GIT, *args], cwd=str(work), check=True, capture_output=True)

    work.mkdir()
    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@example.invalid")
    git("config", "user.name", "t")
    _write(work / ".gitattributes", "* text eol=crlf\n")
    _write(work / "overlays" / "docs" / "overlay.cfg", 'name = "docs"\n')
    _write(work / "overlays" / "docs" / "kb" / "D.md", "line one\nline two\n")
    git("add", "-A")
    git("commit", "-q", "-m", "init")
    commit = subprocess.run([GIT, "rev-parse", "HEAD"], cwd=str(work), capture_output=True,
                            text=True).stdout.strip()
    blob_digest = _aos.tree_digest(work / "overlays" / "docs")  # this work tree holds LF bytes
    repo = "git+%s@main#overlays" % work.as_posix()
    assert _run(OverlayAdd, name=["docs"], repo=[repo], global_scope=True, agents_dir=store, copy=True,
                dry_run=False) == 0
    installed = Overlay(store / "overlays" / "docs")
    assert (installed.path / "kb" / "D.md").read_bytes() == b"line one\nline two\n"
    assert installed.tree_digest() == blob_digest
    record = installed.read_install_record()
    assert record["commit"] == commit and record["digest"] == blob_digest


def test_a_registry_entry_may_be_a_table_with_a_source(tmp_path, store):
    src = _source(tmp_path)
    _write(store / "dotagents.toml",
           '[overlays.docs]\nsource = %s\npin = "%s"\n' % (json.dumps(str(src / "docs")),
                                                       _aos.tree_digest(src / "docs")))
    assert _run(OverlayAdd, name=["docs"], repo=[], global_scope=True, agents_dir=store, copy=True,
                dry_run=False) == 0
    assert Scope("user", store).overlays[0].name == "docs"
    assert _run(OverlaySync, pattern=None, repo=[], global_scope=True, agents_dir=store, copy=True,
                dry_run=False) == 0
