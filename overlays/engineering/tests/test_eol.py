"""`dotagents eol`: endings are counted in bytes, the repository's own attributes
decide what a file should be, and a fix can never empty a file."""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

duho = pytest.importorskip("duho")

MODULE = Path(__file__).resolve().parents[1] / "cmds" / "eol.py"
_spec = importlib.util.spec_from_file_location("eol_cmd", MODULE)
eol = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = eol  # duho resolves field annotations through sys.modules
_spec.loader.exec_module(eol)

GIT_ENV = dict(os.environ, GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)

FILES = {
    "lf.txt": b"one\ntwo\n",
    "crlf.txt": b"one\r\ntwo\r\n",
    "mixed.txt": b"one\r\ntwo\nthree\r\n",
    "empty.txt": b"",
    "oneline.txt": b"no newline at all",
    "image.bin": b"\x89PNG\r\n\x00\x00\r\n",
    "fixtures/captured.http": b"HTTP/1.1 200 OK\r\nServer: x\r\n\r\nbody\n",
    "run.bat": b"@echo off\necho hi\n",
}


def write_tree(root: Path) -> None:
    for name, data in FILES.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True, env=GIT_ENV,
                   capture_output=True)
    write_tree(root)
    (root / ".gitattributes").write_bytes(
        b"* text=auto eol=lf\n*.bat text eol=crlf\nfixtures/** -text\n")
    return root


def run(command, *argv):
    return duho.parse(command, [str(a) for a in argv])()


def test_endings_are_counted_in_bytes():
    assert eol.state_of(b"a\nb\n") == "LF"
    assert eol.state_of(b"a\r\nb\r\n") == "CRLF"
    assert eol.state_of(b"a\r\nb\n") == "MIXED"
    assert eol.state_of(b"a\rb\n") == "MIXED"
    assert eol.state_of(b"no newline") == "NONE"
    assert eol.endings(b"a\r\nb\nc\r") == (1, 1, 1)


def test_convert_goes_both_ways_and_is_stable():
    assert eol.convert(b"a\r\nb\nc\r", "LF") == b"a\nb\nc\n"
    assert eol.convert(b"a\r\nb\nc\r", "CRLF") == b"a\r\nb\r\nc\r\n"
    assert eol.convert(eol.convert(b"a\r\nb\n", "CRLF"), "CRLF") == b"a\r\nb\r\n"


def test_check_names_what_is_wrong_and_exits_1(repo, capsys):
    assert run(eol.Eol.Check, repo) == eol.EXIT_OFFENDERS
    out = capsys.readouterr().out
    lines = [line for line in out.splitlines() if "expected" in line]
    named = sorted(Path(line.split("  ")[-2].strip()).name for line in lines)
    assert named == ["crlf.txt", "mixed.txt", "run.bat"]
    assert "MIXED  (2 CRLF, 1 LF)" in out
    assert "expected CRLF" in next(line for line in lines if "run.bat" in line)


def test_exempt_and_binary_files_are_never_touched(repo, capsys):
    assert run(eol.Eol.Fix, repo) == 0
    assert (repo / "fixtures/captured.http").read_bytes() == FILES["fixtures/captured.http"]
    assert (repo / "image.bin").read_bytes() == FILES["image.bin"]
    assert "1 exempt" in capsys.readouterr().out


def test_fix_converts_to_what_the_attributes_expect(repo):
    assert run(eol.Eol.Fix, repo) == 0
    assert (repo / "crlf.txt").read_bytes() == b"one\ntwo\n"
    assert (repo / "mixed.txt").read_bytes() == b"one\ntwo\nthree\n"
    assert (repo / "run.bat").read_bytes() == b"@echo off\r\necho hi\r\n"
    assert (repo / "lf.txt").read_bytes() == FILES["lf.txt"]
    assert (repo / "oneline.txt").read_bytes() == FILES["oneline.txt"]
    assert run(eol.Eol.Check, repo) == eol.EXIT_OK
    assert not [p for p in repo.rglob(".eol-*")]


def test_dry_run_changes_nothing(repo, capsys):
    assert run(eol.Eol.Fix, repo, "--dry-run") == 0
    assert (repo / "crlf.txt").read_bytes() == FILES["crlf.txt"]
    assert "would fix" in capsys.readouterr().out


def test_a_single_file_is_judged_by_its_own_attributes(repo):
    assert run(eol.Eol.Check, repo / "lf.txt") == eol.EXIT_OK
    assert run(eol.Eol.Check, repo / "run.bat") == eol.EXIT_OFFENDERS
    assert run(eol.Eol.Check, repo / "fixtures" / "captured.http") == eol.EXIT_CANNOT_CHECK


def test_a_gitignored_file_is_not_examined(repo):
    (repo / ".gitignore").write_bytes(b"out/\n")
    (repo / "out").mkdir()
    (repo / "out" / "gen.txt").write_bytes(b"a\r\n")
    run(eol.Eol.Fix, repo)
    assert (repo / "out" / "gen.txt").read_bytes() == b"a\r\n"


def test_outside_git_everything_is_expected_to_be_lf(tmp_path):
    root = tmp_path / "plain"
    root.mkdir()
    write_tree(root)
    assert run(eol.Eol.Check, root) == eol.EXIT_OFFENDERS
    assert run(eol.Eol.Fix, root) == 0
    assert (root / "crlf.txt").read_bytes() == b"one\ntwo\n"
    assert (root / "image.bin").read_bytes() == FILES["image.bin"]


def test_control_bytes_are_reported_on_request(repo, capsys):
    run(eol.Eol.Fix, repo)
    (repo / "notes.md").write_bytes(b"first\npath _target\x08uild\n")
    assert run(eol.Eol.Check, repo) == eol.EXIT_OK
    capsys.readouterr()
    assert run(eol.Eol.Check, repo, "--control") == eol.EXIT_OFFENDERS
    assert "byte 0x08, line 2" in capsys.readouterr().out


def test_a_directory_with_no_text_cannot_be_checked(tmp_path):
    (tmp_path / "only.bin").write_bytes(b"\x00\x01")
    assert run(eol.Eol.Check, tmp_path) == eol.EXIT_CANNOT_CHECK


def test_a_missing_path_is_an_error(tmp_path):
    with pytest.raises(SystemExit, match="no such file"):
        run(eol.Eol.Check, tmp_path / "nope")
