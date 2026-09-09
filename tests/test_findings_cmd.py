"""Tests for the bundled `findings` command module
(`src/dotagents/_overlay/dotagents/cmds/findings.py`): the file/index model
(`Finding`, `FindingsStore`) and the nested subcommands, driven the way
`test_overlays_command.py` drives command classes (set fields, call).

Filesystem-only (tmp_path). The module is loaded by file path exactly as
discovery does (it is not an importable package member).
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

MODULE_PATH = ROOT / "src" / "dotagents" / "_overlay" / "dotagents" / "cmds" / "findings.py"


@pytest.fixture(scope="module")
def findings_mod():
    # Registered in sys.modules BEFORE exec, exactly as discovery imports it:
    # duho resolves a command's field annotations with `typing.get_type_hints`,
    # which reads the class's module globals through `sys.modules[__module__]`.
    spec = importlib.util.spec_from_file_location("test_findings_module", MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    try:
        spec.loader.exec_module(mod)
        yield mod
    finally:
        sys.modules.pop(spec.name, None)


def _run(cmd_cls, **kwargs):
    cmd = cmd_cls()
    for k, v in kwargs.items():
        setattr(cmd, k, v)
    return cmd()


# --------------------------------------------------------------------------- #
# slugify + Finding
# --------------------------------------------------------------------------- #

def test_slugify(findings_mod):
    s = findings_mod.slugify
    assert s("Context inlining missed bare refs!") == "context-inlining-missed-bare-refs"
    assert s("  --Already--slug--  ") == "already-slug"
    assert s("x" * 100).startswith("x") and len(s("x" * 100)) == 60
    assert s("???") == ""


def test_finding_load_hand_written_note(findings_mod, tmp_path):
    """A note with no frontmatter is still a finding: stem name, first heading
    as description, status from its directory."""
    p = tmp_path / "findings" / "old-note.md"
    p.parent.mkdir(parents=True)
    p.write_text("\n# Old note title\n\nbody\n", encoding="utf-8")
    f = findings_mod.Finding.load(p)
    assert f.name == "old-note"
    assert f.description == "Old note title"
    assert f.status == "active" and f.is_active
    q = tmp_path / "findings" / "processed" / "done-note.md"
    q.parent.mkdir(parents=True)
    q.write_text("plain first line\n", encoding="utf-8")
    assert findings_mod.Finding.load(q).status == "processed"


def test_finding_render_roundtrip_is_lf_only(findings_mod, tmp_path):
    f = findings_mod.Finding(
        tmp_path / "f.md",
        {"name": "f", "description": "d", "status": "active", "created": "2026-09-09"},
        "body line\n",
    )
    f.save()
    raw = f.path.read_bytes()
    assert b"\r\n" not in raw
    assert raw.startswith(b"---\nname: f\ndescription: d\nstatus: active\ncreated: 2026-09-09\n---\n\nbody line\n")
    again = findings_mod.Finding.load(f.path)
    assert again.meta == f.meta and again.body == "body line\n"


# --------------------------------------------------------------------------- #
# FindingsStore: add / done / reopen / remove / index
# --------------------------------------------------------------------------- #

def test_store_add_done_reopen_remove(findings_mod, tmp_path):
    store = findings_mod.FindingsStore(tmp_path / "findings")
    f = store.add("Bare refs were never inlined", body="details")
    assert f.path == store.root / "bare-refs-were-never-inlined.md"
    assert f.path.is_file()
    assert store.index_path.is_file()
    assert [x.name for x in store.active()] == ["bare-refs-were-never-inlined"]
    assert store.processed() == []
    idx = store.index_path.read_text(encoding="utf-8")
    assert "- [bare-refs-were-never-inlined](bare-refs-were-never-inlined.md) — Bare refs were never inlined" in idx
    assert "## Processed\n\n(none)" in idx

    # done: resolution appended, status/date set, file MOVED to processed/.
    d = store.done("bare-refs-were-never-inlined", "fixed in _context")
    assert d.path == store.processed_dir / "bare-refs-were-never-inlined.md"
    assert d.path.is_file() and not f.path.exists()
    text = d.path.read_text(encoding="utf-8")
    assert "status: processed" in text and "processed: " in text
    assert "## Resolution (" in text and text.rstrip().endswith("fixed in _context")
    assert store.active() == [] and [x.name for x in store.processed()] == ["bare-refs-were-never-inlined"]
    idx = store.index_path.read_text(encoding="utf-8")
    assert "## Active\n\n(none)" in idx
    assert "(processed/bare-refs-were-never-inlined.md)" in idx and "(processed 20" in idx
    # get() finds it in processed/ too, by name or file name.
    assert store.get("bare-refs-were-never-inlined.md").status == "processed"
    assert store.get("Bare refs were never inlined") is not None  # slugified lookup

    # reopen: back to active, resolution history kept.
    r = store.reopen("bare-refs-were-never-inlined")
    assert r.path == store.root / "bare-refs-were-never-inlined.md" and r.path.is_file()
    assert "## Resolution (" in r.path.read_text(encoding="utf-8")
    assert "processed:" not in r.render().split("---")[1]

    # remove: gone, index updated.
    gone = store.remove("bare-refs-were-never-inlined")
    assert not gone.exists()
    assert "(none)" in store.index_path.read_text(encoding="utf-8")


def test_store_done_requires_resolution_and_rejects_double(findings_mod, tmp_path):
    store = findings_mod.FindingsStore(tmp_path / "findings")
    store.add("thing", name="thing")
    with pytest.raises(SystemExit, match="resolution is required"):
        store.done("thing", "   ")
    store.done("thing", "ok")
    with pytest.raises(SystemExit, match="already processed"):
        store.done("thing", "again")
    with pytest.raises(SystemExit, match="no finding"):
        store.require("missing")


def test_store_add_rejects_duplicates_and_empty(findings_mod, tmp_path):
    store = findings_mod.FindingsStore(tmp_path / "findings")
    store.add("dup", name="dup")
    with pytest.raises(SystemExit, match="already exists"):
        store.add("dup again", name="dup")
    store.done("dup", "r")
    with pytest.raises(SystemExit, match="already exists"):
        store.add("dup", name="dup")  # a processed one blocks the name too
    with pytest.raises(SystemExit, match="one-line description"):
        store.add("   ")
    with pytest.raises(SystemExit, match="no usable file name"):
        store.add("???")


def test_store_lists_hand_written_notes_and_skips_index(findings_mod, tmp_path):
    root = tmp_path / "findings"
    root.mkdir()
    (root / "INDEX.md").write_text("# stale\n", encoding="utf-8")
    (root / "README.md").write_text("about\n", encoding="utf-8")
    (root / "_draft.md").write_text("x\n", encoding="utf-8")
    (root / "note.md").write_text("# A hand-written note\n", encoding="utf-8")
    store = findings_mod.FindingsStore(root)
    assert [f.name for f in store.active()] == ["note"]
    # done on a hand-written note gives it a frontmatter.
    d = store.done("note", "handled")
    assert d.path.read_text(encoding="utf-8").startswith("---\nname: note\ndescription: A hand-written note\nstatus: processed\n")


# --------------------------------------------------------------------------- #
# The command classes: scope resolution + output shapes
# --------------------------------------------------------------------------- #

def test_cmd_scope_default_project_and_global(findings_mod, tmp_path, monkeypatch):
    F = findings_mod.Findings
    proj = tmp_path / "proj"
    home = tmp_path / "home"
    proj.mkdir(); home.mkdir()
    monkeypatch.setenv("AGENTS_PROJECT_ROOT", str(proj))

    assert _run(F.Add, description="Project finding") == 0
    assert (proj / ".agents" / "findings" / "project-finding.md").is_file()
    assert _run(F.Add, description="Global finding", global_scope=True, agents_dir=home) == 0
    assert (home / "findings" / "global-finding.md").is_file()
    assert not (home / "findings" / "project-finding.md").exists()
    # --dir wins over both.
    elsewhere = tmp_path / "elsewhere"
    assert _run(F.Add, description="Elsewhere", dir=elsewhere) == 0
    assert (elsewhere / "elsewhere.md").is_file()


def test_cmd_list_show_json_and_text(findings_mod, tmp_path, monkeypatch, capsys):
    F = findings_mod.Findings
    d = tmp_path / "q"
    _run(F.Add, description="First", name="first", body="details of first", dir=d)
    _run(F.Add, description="Second", name="second", dir=d)
    _run(F.Done, name="second", resolution="done because", dir=d)
    capsys.readouterr()  # drain the paths add/done print

    _run(F.List, dir=d)
    assert capsys.readouterr().out == "first: First\n"
    _run(F.List, dir=d, all=True)
    out = capsys.readouterr().out
    assert "[active] first: First" in out and "[processed] second: Second" in out
    _run(F.List, dir=d, processed=True)
    assert capsys.readouterr().out == "second: Second\n"

    _run(F.List, dir=d, all=True, as_json=True)
    payload = json.loads(capsys.readouterr().out)
    assert payload["dir"] == str(d)
    assert [f["name"] for f in payload["findings"]] == ["first", "second"]
    assert payload["findings"][1]["status"] == "processed"
    assert "body" not in payload["findings"][0]

    _run(F.Show, name="first", dir=d)
    out = capsys.readouterr().out
    assert out.startswith("---\nname: first\n") and "details of first" in out
    _run(F.Show, name="second", dir=d, as_json=True)
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "processed" and "done because" in payload["body"]
    assert payload["path"].endswith("second.md")

    _run(F.PathCmd, dir=d)
    assert capsys.readouterr().out.strip() == str(d)
    _run(F.Index, dir=d)
    assert capsys.readouterr().out.strip() == str(d / "INDEX.md")


def test_cmd_body_and_resolution_from_file_and_stdin(findings_mod, tmp_path, monkeypatch, capsys):
    F = findings_mod.Findings
    d = tmp_path / "q"
    body = tmp_path / "body.md"
    body.write_text("from a file\n", encoding="utf-8")
    _run(F.Add, description="Filed", name="filed", body_file=body, dir=d)
    assert "from a file" in (d / "filed.md").read_text(encoding="utf-8")

    import io
    monkeypatch.setattr(sys, "stdin", io.StringIO("resolved via stdin\n"))
    _run(F.Done, name="filed", resolution_file=Path("-"), dir=d)
    assert "resolved via stdin" in (d / "processed" / "filed.md").read_text(encoding="utf-8")
    _run(F.Reopen, name="filed", dir=d)
    assert (d / "filed.md").is_file()
    _run(F.Remove, name="filed", dir=d)
    assert not (d / "filed.md").exists()
    capsys.readouterr()


def test_cmd_done_without_resolution_fails(findings_mod, tmp_path):
    F = findings_mod.Findings
    d = tmp_path / "q"
    _run(F.Add, description="x", name="x", dir=d)
    with pytest.raises(SystemExit, match="resolution is required"):
        _run(F.Done, name="x", dir=d)
    assert (d / "x.md").is_file()  # untouched


def test_subcommands_are_nested_not_module_level(findings_mod):
    """Discovery collects module-level Cmd subclasses; the subcommands must NOT
    be module-level or they'd register as top-level `dotagents add` etc."""
    from duho import Cmd

    module_level = [
        name for name, obj in vars(findings_mod).items()
        if isinstance(obj, type) and issubclass(obj, Cmd) and obj.__module__ == findings_mod.__name__
    ]
    assert module_level == ["Findings"]
    names = [c._parsername_ for c in findings_mod.Findings._subcommands_]
    assert names == ["add", "list", "show", "done", "reopen", "remove", "index", "path"]
