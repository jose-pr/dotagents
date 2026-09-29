"""`env --into FILE` keeps ONE dotagents block in a hook's env file however
often the hook runs, leaves other hooks' lines alone, and `--diff --format
export` writes PATH relative to the sourcing shell's own -- so the file
Claude Code inlines into every Bash command stays small. (A SessionStart hook
that appended on every resume/compact grew it past Git for Windows' ~8 KB
`bash -c` limit, and every Bash call failed.)"""
import os
import subprocess

import pytest

from dotagents.cli.env import INTO_BEGIN, INTO_END, _format_env, _prefixed_path_line, write_into
from _shell import real_bash


def test_write_into_replaces_only_its_own_block(tmp_path):
    env_file = tmp_path / "hook.sh"
    env_file.write_text("export OTHER_HOOK='kept'\n", encoding="utf-8")
    for value in ("one", "two", "three"):
        write_into(env_file, "export A='%s'\nexport B='x'\n" % value, "export")
    text = env_file.read_text(encoding="utf-8")
    assert text.count(INTO_BEGIN) == 1 and text.count(INTO_END) == 1, "one block, however often"
    assert "export OTHER_HOOK='kept'" in text, "another hook's line stays"
    assert "export A='three'" in text and "one" not in text and "two" not in text
    assert text.index("OTHER_HOOK") < text.index(INTO_BEGIN), "ours last: it wins, as an append did"
    assert b"\r" not in env_file.read_bytes()


def test_write_into_creates_the_file_and_refuses_formats_without_comments(tmp_path):
    env_file = tmp_path / "new.sh"
    write_into(env_file, "export A='1'\n", "export")
    assert env_file.read_text(encoding="utf-8") == "%s\nexport A='1'\n%s\n" % (INTO_BEGIN, INTO_END)
    with pytest.raises(SystemExit):
        write_into(tmp_path / "x.cmd", 'set "A=1"\n', "cmd")


def test_a_command_appended_to_the_block_still_runs(tmp_path):
    """The markers are no-op COMMANDS: a harness that inlines the file and
    joins the command onto its last line (`; cmd`, `&& cmd`) must not have it
    commented out."""
    bash = real_bash()
    if not bash:
        pytest.skip("no bash")
    env_file = tmp_path / "hook.sh"
    write_into(env_file, "export A='1'\n", "export")
    body = env_file.read_text(encoding="utf-8").rstrip("\n")
    for joiner in ("; ", " && ", "\n"):
        out = subprocess.run([bash, "-c", body + joiner + 'printf "%s" "$A"'], capture_output=True, text=True)
        assert out.stdout == "1", (joiner, out.stderr)
    with pytest.raises(SystemExit):
        write_into(tmp_path / "x.env", "A=1\n", "dotenv")


def test_prefixed_path_line():
    assert _prefixed_path_line("/a:/b:/usr/bin", "/usr/bin", ":") == 'export PATH=\'/a:/b\'"${PATH:+:$PATH}"'
    assert _prefixed_path_line("/usr/bin", "/usr/bin", ":") is None, "nothing in front"
    assert _prefixed_path_line("/a:/usr/local/bin", "/usr/bin", ":") is None, "not a prefix: full value"
    assert _prefixed_path_line("/a", "", ":") is None


@pytest.mark.skipif(os.name == "nt", reason="a POSIX PATH; the Windows conversion is covered below")
def test_diff_export_writes_path_as_a_prefix_on_posix():
    out = _format_env({"PATH": "/opt/x/bin:/usr/bin:/bin", "A": "1"}, "export", base={"PATH": "/usr/bin:/bin"})
    assert "export PATH='/opt/x/bin'\"${PATH:+:$PATH}\"" in out
    assert _format_env({"PATH": "/opt/x/bin:/usr/bin"}, "export") == "export PATH='/opt/x/bin:/usr/bin'", "no base: full"


def test_diff_export_prefix_survives_the_windows_conversion(monkeypatch):
    from dotagents.cli import env as env_cli

    monkeypatch.setattr(env_cli, "_host_is_windows", lambda: True)
    base = {"PATH": r"C:\Windows\System32;C:\Windows"}
    new = {"PATH": r"C:\Tools\dotagents\bin;C:\Windows\System32;C:\Windows"}
    out = _format_env(new, "export", base=base)
    assert out == "export PATH='/c/Tools/dotagents/bin'\"${PATH:+:$PATH}\""


def test_a_sourced_block_really_prepends(tmp_path):
    """Source the rendered line in a real POSIX shell: the prefix lands in
    front of that shell's own PATH."""
    bash = real_bash()
    if not bash:
        pytest.skip("no bash")
    line = _prefixed_path_line("/opt/dotagents/bin:/usr/bin", "/usr/bin", ":")
    script = tmp_path / "env.sh"
    script.write_text(line + "\n", encoding="utf-8")
    out = subprocess.run([bash, "-c", "PATH=/shell/own; . ./env.sh; printf %s \"$PATH\""],
                         cwd=str(tmp_path), capture_output=True, text=True)
    assert out.stdout == "/opt/dotagents/bin:/shell/own", out.stderr
