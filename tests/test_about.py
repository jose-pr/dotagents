"""`dotagents about`: the CLI first, then one `<distribution> <version>` per
line -- the bundle's packages from a .pyz, the installed dependencies from a
plain install -- and the build-pyz side that records the bundle."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dotagents import __version__  # noqa: E402
from dotagents.cli import about  # noqa: E402
from dotagents.cli.build_pyz import _dist_info_name_version  # noqa: E402


def _run(**kwargs):
    cmd = about.About()
    for k, v in kwargs.items():
        setattr(cmd, k, v)
    return cmd()


def test_plain_install_lists_the_cli_then_its_dependencies(capsys, monkeypatch):
    monkeypatch.setattr(about, "bundle_manifest", lambda: None)
    assert _run() == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "dotagents-cli %s" % __version__
    names = [line.split(" ")[0] for line in lines[1:]]
    assert names[:2] == ["duho", "pathlib_next"], "the runtime dependencies, in order"
    for line in lines[1:]:
        name, version = line.split(" ", 1)
        assert version and version[0].isdigit(), line


def test_a_bundle_lists_exactly_what_was_vendored(capsys, monkeypatch):
    monkeypatch.setattr(about, "bundle_manifest", lambda: {
        "packages": {"duho": "0.5.0", "pathlib_next": "0.9.0", "uritools": "5.0.0"},
        "extras": "uri", "python": "3.14.7",
    })
    assert _run() == 0
    assert capsys.readouterr().out == (
        "dotagents-cli %s\nduho 0.5.0\npathlib_next 0.9.0\nuritools 5.0.0\n" % __version__
    )
    assert _run(json=True) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["distribution"] == "dotagents-cli" and payload["version"] == __version__
    assert payload["bundle"] is True and payload["built"] == {"extras": "uri", "python": "3.14.7"}
    assert payload["packages"]["uritools"] == "5.0.0" and payload["python"].count(".") == 2


def test_dist_info_metadata_is_read_name_and_version(tmp_path):
    d = tmp_path / "duho-0.5.0.dist-info"
    d.mkdir()
    (d / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: duho\nVersion: 0.5.0\nSummary: x\n\nlong description Name: not-this\n",
        encoding="utf-8",
    )
    assert _dist_info_name_version(d) == {"duho": "0.5.0"}
    assert _dist_info_name_version(tmp_path / "missing.dist-info") == {}


def test_about_is_a_builtin_command(monkeypatch, tmp_path):
    from dotagents import cli

    for v in ("AGENTS_PROJECT_ROOT", "AGENTS_HOME", "AGENTS_CMDS_PATH"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("AGENTS_HOME", str(tmp_path / "store"))
    monkeypatch.chdir(tmp_path)
    names = {getattr(c, "_parsername_", None) for c in cli._discover([])}
    assert "about" in names
