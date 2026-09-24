"""Quoting and injection fixes from the 2026-09-23 review: the CwdChanged hook's
path, PowerShell's typographic quotes, the env-file path handed to bash,
`DOTAGENTS_*` in `env`'s full output, and git option injection through a spec.

tmp dirs only; HOME/USERPROFILE and the scope vars are redirected.
"""

import json
import os
import subprocess
from pathlib import Path

import pytest

from _shell import BASH

from dotagents import _env, _sources
from dotagents._agents import ClaudeAgent
from dotagents.cli.env import _format_env

needs_bash = pytest.mark.skipif(BASH is None, reason="needs a working bash")


@needs_bash
@pytest.mark.parametrize("name", ["john's project", "x';touch PWNED;'"])
def test_cwd_changed_pin_round_trips_any_directory_name(tmp_path, name):
    """The hook wrote `export AGENTS_PROJECT_ROOT='<cwd>'` unquoted: a `'` left
    an unterminated quote that broke every later source, and a crafted name
    ran commands before every Bash call."""
    project = tmp_path / name
    (project / ".agents").mkdir(parents=True)
    env_file = tmp_path / "claude_env.sh"
    env = dict(os.environ, CLAUDE_ENV_FILE=str(env_file))
    run = subprocess.run([BASH, "-c", ClaudeAgent.CWD_CHANGED_COMMAND],
                         cwd=str(project), env=env, capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    sourced = subprocess.run(
        [BASH, "-c", 'source "$1" && printf %s "$AGENTS_PROJECT_ROOT"', "bash", str(env_file)],
        cwd=str(tmp_path), capture_output=True, text=True,
    )
    assert sourced.returncode == 0, sourced.stderr
    assert not (tmp_path / "PWNED").exists(), "the directory name ran as a command"
    assert sourced.stdout.replace("\\", "/").endswith("/" + name)


def test_powershell_doubles_every_single_quote_character():
    """PowerShell also reads U+2018..U+201B as single quotes: an undoubled `’`
    ended the string and the rest ran in the Invoke-Expression loader."""
    value = "x\u2019; Write-Output 'RAN'; \u2018"
    out = _format_env({"K": value, "BAD}NAME": "v", "BAD`NAME": "v"}, "powershell")
    assert out == "${env:K} = 'x\u2019\u2019; Write-Output ''RAN''; \u2018\u2018'"


@pytest.mark.skipif(os.name != "nt", reason="PowerShell parser")
def test_powershell_output_parses_as_one_assignment():
    out = _format_env({"K": "C:\\Users\\Bob\u2019s project; Write-Output x"}, "powershell")
    check = ("$t=$null;$e=$null;$a=[System.Management.Automation.Language.Parser]::"
             "ParseInput($env:CODE,[ref]$t,[ref]$e);"
             "\"$($e.Count) $($a.EndBlock.Statements.Count)\"")
    res = subprocess.run(["powershell", "-NoProfile", "-Command", check],
                         capture_output=True, text=True, env=dict(os.environ, CODE=out))
    assert res.stdout.strip() == "0 1", (res.stdout, res.stderr)


@needs_bash
@pytest.mark.parametrize("dirname", ["Jos\u00e9", "cost$HOME", "x`touch MARK`y"])
def test_env_file_under_an_odd_path_is_sourced(tmp_path, monkeypatch, dirname):
    """The path went into `bash -c` JSON-quoted: non-ASCII became a literal
    `\\u00e9` and `$` / backticks expanded, so the file was silently dropped."""
    monkeypatch.setenv("PATH", str(Path(BASH).parent) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.chdir(tmp_path)
    env_file = tmp_path / dirname / "env"
    env_file.parent.mkdir()
    env_file.write_text("export ONLY=me\n", encoding="utf-8")
    changes = _env.get_env_from_file(env_file, base_env=dict(os.environ))
    assert changes.get("ONLY") == "me"
    assert not (tmp_path / "MARK").exists()


def test_full_env_output_leaves_inherited_dotagents_values_out(tmp_path, monkeypatch, capsys):
    from dotagents.cli.env import Env

    for var in ("AGENTS_PROJECT_ROOT", "CLAUDE_PROJECT_DIR"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("AGENTS_HOME", str(tmp_path / "store"))
    monkeypatch.setenv("DOTAGENTS_AGENTS_TOKEN", "ghp_FAKE")
    monkeypatch.chdir(tmp_path)
    cmd = Env()
    cmd.format = "json"
    assert cmd() == 0
    out = json.loads(capsys.readouterr().out)
    assert "DOTAGENTS_AGENTS_TOKEN" not in out and "ghp_FAKE" not in json.dumps(out)
    assert out.get("AGENTS_HOME")  # the rest of the environment is still there


@pytest.mark.parametrize("spec", [
    "https://example.com/r.git@--upload-pack=touch PWNED",
    "--upload-pack=touch PWNED.git",
])
def test_a_git_spec_cannot_smuggle_an_option(spec):
    with pytest.raises(SystemExit, match="must not start with '-'"):
        _sources.parse_spec(spec)
