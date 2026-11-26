"""Tests for the bundled `findings` command module
(`src/dotagents/_overlay/dotagents/cmds/findings.py`): AI6-report frontmatter,
status folders (`active/`, `processed/`), SQL query, set/edit/remove/migrate, no INDEX.md.
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest
from _helpers import run_cmd as _run

ROOT = Path(__file__).resolve().parents[1]

MODULE_PATH = ROOT / "src" / "dotagents" / "_overlay" / "dotagents" / "cmds" / "findings.py"


@pytest.fixture(scope="module")
def findings_mod():
    spec = importlib.util.spec_from_file_location("test_findings_module", MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    try:
        spec.loader.exec_module(mod)
        yield mod
    finally:
        sys.modules.pop(spec.name, None)


def test_slugify(findings_mod):
    s = findings_mod.slugify
    assert s("Context inlining missed bare refs!") == "context-inlining-missed-bare-refs"
    assert s("  --Already--slug--  ") == "already-slug"
    assert s("x" * 100).startswith("x") and len(s("x" * 100)) == 60
    assert s("???") == ""


def test_finding_load_hand_written_note(findings_mod, tmp_path):
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
    assert raw.startswith(b"---\ndescription: d\ncreated: 2026-09-09\n---\n\nbody line\n")
    again = findings_mod.Finding.load(f.path)
    assert again.meta == f.meta and again.body == "body line\n"


def test_store_add_done_reopen_remove_without_index(findings_mod, tmp_path):
    store = findings_mod.FindingsStore(tmp_path / "findings")
    f = store.add(
        "Bare refs were never inlined",
        body="details",
        attrs={"severity": "medium", "category": "docs"},
    )
    assert f.path == store.active_dir / "bare-refs-were-never-inlined.md"
    assert f.path.is_file()
    assert not (store.root / "INDEX.md").exists()
    assert [x.name for x in store.active()] == ["bare-refs-were-never-inlined"]
    assert store.processed() == []

    # done: resolution appended, status/date set, file MOVED to processed/.
    d = store.done("bare-refs-were-never-inlined", "fixed in _context")
    assert d.path == store.processed_dir / "bare-refs-were-never-inlined.md"
    assert d.path.is_file() and not f.path.exists()
    text = d.path.read_text(encoding="utf-8")
    assert "status:" not in text and "processed: " in text
    assert "## Resolution (" in text and text.rstrip().endswith("fixed in _context")
    assert store.active() == [] and [x.name for x in store.processed()] == ["bare-refs-were-never-inlined"]
    assert not (store.root / "INDEX.md").exists()
    assert store.get("bare-refs-were-never-inlined.md").status == "processed"
    assert store.get("Bare refs were never inlined") is not None

    # reopen: back to active/, resolution history kept.
    r = store.reopen("bare-refs-were-never-inlined")
    assert r.path == store.active_dir / "bare-refs-were-never-inlined.md" and r.path.is_file()
    assert "## Resolution (" in r.path.read_text(encoding="utf-8")
    assert "processed:" not in r.render().split("---")[1]

    # remove: gone
    gone = store.remove("bare-refs-were-never-inlined")
    assert not gone.exists()


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
        store.add("dup", name="dup")
    with pytest.raises(SystemExit, match="one-line description"):
        store.add("   ")
    with pytest.raises(SystemExit, match="no usable file name"):
        store.add("???")


def test_store_add_rejects_a_name_taken_by_frontmatter(findings_mod, tmp_path):
    root = tmp_path / "findings"
    root.mkdir()
    (root / "foo.md").write_text("---\nname: bar\ndescription: hand note\n---\nbody\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="already exists"):
        findings_mod.FindingsStore(root).add("Something", name="bar")


def test_store_lists_hand_written_notes_and_skips_index(findings_mod, tmp_path):
    root = tmp_path / "findings"
    root.mkdir()
    (root / "INDEX.md").write_text("# stale\n", encoding="utf-8")
    (root / "README.md").write_text("about\n", encoding="utf-8")
    (root / "_draft.md").write_text("x\n", encoding="utf-8")
    (root / "note.md").write_text("# A hand-written note\n", encoding="utf-8")
    store = findings_mod.FindingsStore(root)
    assert [f.name for f in store.active()] == ["note"]
    d = store.done("note", "handled")
    assert d.path.read_text(encoding="utf-8").startswith("---\ndescription: A hand-written note\n")


def test_cmd_scope_default_project_and_global(findings_mod, tmp_path, monkeypatch):
    F = findings_mod.Findings
    proj = tmp_path / "proj"
    home = tmp_path / "home"
    proj.mkdir(); home.mkdir()
    monkeypatch.setenv("AGENTS_PROJECT_ROOT", str(proj))
    monkeypatch.chdir(proj)

    assert _run(F.Add, description="Project finding") == 0
    assert (proj / ".agents" / "findings" / "active" / "project-finding.md").is_file()
    assert _run(F.Add, description="Global finding", global_scope=True, agents_dir=home) == 0
    assert (home / "findings" / "active" / "global-finding.md").is_file()
    assert not (home / "findings" / "active" / "project-finding.md").exists()

    pinned = tmp_path / "pinned-store"
    monkeypatch.setenv("AGENTS_HOME", str(pinned))
    assert _run(F.Add, description="Pinned finding", global_scope=True) == 0
    assert (pinned / "findings" / "active" / "pinned-finding.md").is_file()
    assert not (Path.home() / ".agents" / "findings" / "active" / "pinned-finding.md").exists()

    assert _run(F.Add, description="Explicit dir", global_scope=True, agents_dir=home) == 0
    assert (home / "findings" / "active" / "explicit-dir.md").is_file()
    assert not (pinned / "findings" / "active" / "explicit-dir.md").exists()

    elsewhere = tmp_path / "elsewhere"
    assert _run(F.Add, description="Elsewhere", dir=elsewhere) == 0
    assert (elsewhere / "active" / "elsewhere.md").is_file()


def test_cmd_list_query_show_set_edit_and_migrate(findings_mod, tmp_path, capsys):
    F = findings_mod.Findings
    d = tmp_path / "q"
    _run(F.Add, description="First", name="first", severity="high", category="security", body="details of first", dir=d)
    _run(F.Add, description="Second", name="second", severity="low", category="docs", dir=d)
    _run(F.Done, name="second", resolution="done because", dir=d)
    capsys.readouterr()

    _run(F.List, dir=d)
    assert capsys.readouterr().out == "first: First\n"
    _run(F.List, dir=d, all=True)
    out = capsys.readouterr().out
    assert "[active] first: First" in out and "[processed] second: Second" in out
    _run(F.List, dir=d, processed=True)
    assert capsys.readouterr().out == "second: Second\n"

    _run(F.Query, dir=d, sql="SELECT name, severity FROM findings WHERE severity = 'high'", fmt="json")
    q_out = json.loads(capsys.readouterr().out)
    assert q_out == [{"name": "first", "severity": "high"}]

    _run(F.Set, name="first", attrs=["credibility=1", "links=[a,b]"], dir=d)
    capsys.readouterr()
    _run(F.Edit, name="first", append="## Extra\n\nverified", dir=d)
    capsys.readouterr()
    _run(F.Show, name="first", dir=d, as_json=True)
    shown = json.loads(capsys.readouterr().out)
    assert shown["credibility"] == 1
    assert shown["links"] == ["a", "b"]
    assert "verified" in shown["body"]

    _run(F.PathCmd, dir=d)
    assert capsys.readouterr().out.strip() == str(d)


def test_cmd_body_and_resolution_from_file_and_stdin(findings_mod, tmp_path, monkeypatch, capsys):
    F = findings_mod.Findings
    d = tmp_path / "q"
    body = tmp_path / "body.md"
    body.write_text("from a file\n", encoding="utf-8")
    _run(F.Add, description="Filed", name="filed", body_file=body, dir=d)
    assert "from a file" in (d / "active" / "filed.md").read_text(encoding="utf-8")

    import io
    monkeypatch.setattr(sys, "stdin", io.StringIO("resolved via stdin\n"))
    _run(F.Done, name="filed", resolution_file=Path("-"), dir=d)
    assert "resolved via stdin" in (d / "processed" / "filed.md").read_text(encoding="utf-8")
    _run(F.Reopen, name="filed", dir=d)
    assert (d / "active" / "filed.md").is_file()
    _run(F.Remove, name="filed", dir=d)
    assert not (d / "active" / "filed.md").exists()
    capsys.readouterr()


def test_cmd_done_without_resolution_fails(findings_mod, tmp_path):
    F = findings_mod.Findings
    d = tmp_path / "q"
    _run(F.Add, description="x", name="x", dir=d)
    with pytest.raises(SystemExit, match="resolution is required"):
        _run(F.Done, name="x", dir=d)
    assert (d / "active" / "x.md").is_file()


def test_subcommands_are_nested_not_module_level(findings_mod):
    from duho import Cmd

    module_level = [
        name for name, obj in vars(findings_mod).items()
        if isinstance(obj, type) and issubclass(obj, Cmd) and obj.__module__ == findings_mod.__name__
    ]
    assert module_level == ["Findings"]
    names = [c._parsername_ for c in findings_mod.Findings._subcommands_]
    assert names == [
        "add", "list", "query", "show", "set", "edit", "done",
        "reopen", "remove", "requote", "migrate", "providers", "path",
    ]


def test_cmd_body_from_stdin_is_read_as_utf8_bytes(findings_mod, tmp_path, monkeypatch, capsys):
    import io

    F = findings_mod.Findings
    d = tmp_path / "q"
    text = "arrow → and café\n"
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(text.encode("utf-8")), encoding="cp1252"))
    _run(F.Add, description="Piped", name="piped", body_file=Path("-"), dir=d)
    capsys.readouterr()
    assert text.strip() in (d / "active" / "piped.md").read_text(encoding="utf-8")


def test_a_non_utf8_note_does_not_break_the_queue(findings_mod, tmp_path):
    root = tmp_path / "findings"
    root.mkdir()
    (root / "handnote.md").write_bytes(b"caf\xe9 note\n")
    store = findings_mod.FindingsStore(root)
    store.add("Second finding", name="second")
    assert "second" in [f.name for f in store.active()]


def test_a_bom_note_keeps_its_frontmatter(findings_mod, tmp_path):
    root = tmp_path / "findings"
    root.mkdir()
    (root / "bom.md").write_bytes(
        b"\xef\xbb\xbf---\nname: bom\ndescription: written by PowerShell 5\n---\nbody\n"
    )
    store = findings_mod.FindingsStore(root)
    assert [(f.name, f.description) for f in store.active()] == [("bom", "written by PowerShell 5")]


def test_a_finding_named_like_reserved_is_refused(findings_mod, tmp_path):
    root = tmp_path / "findings"
    store = findings_mod.FindingsStore(root)
    for title in ("Index", "README", "active", "processed"):
        with pytest.raises(SystemExit, match="reserved"):
            store.add(title, body="important details")
    assert not root.exists() or not [p for p in root.iterdir() if p.suffix == ".md"]


AWKWARD = [
    "[low][correctness] off Linux, sniff with an interface name raises",
    "`dotagents pyvenv` broken on the new machine",
    "perf rule: two hosted-runner runs are not a before/after",
    '"It compiles" is not evidence',
    "'first', 'second' and a third",
    '"quoted"',
    "ends with a colon:",
    "a marker # that starts a comment",
    "*starred* and &anchored",
    'a back\\slash and a "quote": both',
    "- a leading dash",
    "{braces} and a percent %",
    "em dash — and an accent: é",
    "true",
]


@pytest.mark.parametrize("text", AWKWARD)
def test_any_description_is_frontmatter_yaml_can_read(findings_mod, tmp_path, text):
    yaml = pytest.importorskip("yaml")
    store = findings_mod.FindingsStore(tmp_path / "findings")
    finding = store.add(text, name="x")
    front = yaml.safe_load(finding.path.read_text(encoding="utf-8").split("---\n")[1])
    assert "name" not in front and str(front["description"]) == text
    assert findings_mod.Finding.load(finding.path).description == text


def test_a_plain_description_is_written_bare(findings_mod, tmp_path):
    store = findings_mod.FindingsStore(tmp_path / "findings")
    text = "bare kb/X.md refs were never inlined (3 files, 2026-09-09)"
    raw = store.add(text).path.read_text(encoding="utf-8")
    assert "\ndescription: %s\n" % text in raw


@pytest.mark.parametrize("text", [
    "[low][api] a consumer: rewrites `x`",
    '"It compiles" is not evidence',
    "'one', 'two'",
    '"a" and "b"',
])
def test_a_finding_written_bare_still_reads_as_it_did(findings_mod, tmp_path, text):
    path = tmp_path / "findings" / "old.md"
    path.parent.mkdir(parents=True)
    path.write_bytes(("---\nname: old\ndescription: %s\nstatus: active\n---\n\nbody\n" % text).encode("utf-8"))
    assert findings_mod.Finding.load(path).description == text


def test_migrate_and_requote_move_legacy_files_and_drop_index(findings_mod, tmp_path, capsys):
    yaml = pytest.importorskip("yaml")
    root = tmp_path / "findings"
    (root / "processed").mkdir(parents=True)
    (root / "INDEX.md").write_text("# old\n", encoding="utf-8")
    bad = root / "processed" / "bad.md"
    bad.write_bytes(b"---\r\nname: bad\r\ndescription: [low][api] a: b\r\nstatus: processed\r\n---\r\n\r\n\r\nbody kept  \r\n\r\n\r\n")
    (root / "legacy.md").write_text("---\nname: legacy\ndescription: old active\n---\nbody\n", encoding="utf-8")

    assert _run(findings_mod.Findings.Migrate, dir=root) == 0
    res = json.loads(capsys.readouterr().out)
    assert res["removed_index"] is True
    assert not (root / "INDEX.md").exists()
    assert (root / "active" / "legacy.md").is_file()
    assert not (root / "legacy.md").exists()
    assert yaml.safe_load(bad.read_bytes().decode().split("---\r\n")[1])["description"] == "[low][api] a: b"
    assert findings_mod.FindingsStore(root).requote() == []


def test_migrate_strips_name_and_status_and_later_writes_keep_them_out(findings_mod, tmp_path, capsys):
    root = tmp_path / "findings"
    (root / "active").mkdir(parents=True)
    (root / "processed").mkdir()
    (root / "active" / "one.md").write_text("---\nname: one\ndescription: first\nstatus: active\nseverity: low\n---\n\nbody\n", encoding="utf-8")
    (root / "processed" / "two.md").write_text("---\nname: two\ndescription: second\nstatus: processed\n---\n\nbody\n", encoding="utf-8")
    assert _run(findings_mod.Findings.Migrate, dir=root, dry_run=True) == 0
    assert sorted(json.loads(capsys.readouterr().out)["stripped"]) == ["one", "two"]
    assert "name: one" in (root / "active" / "one.md").read_text(encoding="utf-8")

    assert _run(findings_mod.Findings.Migrate, dir=root) == 0
    assert sorted(json.loads(capsys.readouterr().out)["stripped"]) == ["one", "two"]
    for path, status in ((root / "active" / "one.md", "active"), (root / "processed" / "two.md", "processed")):
        head = path.read_text(encoding="utf-8").split("---\n")[1]
        assert "name:" not in head and "status:" not in head and "description:" in head
        found = findings_mod.FindingsStore(root).require(path.stem)
        assert (found.name, found.status) == (path.stem, status)
    assert "severity: low" in (root / "active" / "one.md").read_text(encoding="utf-8")

    store = findings_mod.FindingsStore(root)
    store.add("third thing")
    store.done("one", "fixed")
    for path in (root / "active" / "third-thing.md", root / "processed" / "one.md"):
        head = path.read_text(encoding="utf-8").split("---\n")[1]
        assert "name:" not in head and "status:" not in head
    assert _run(findings_mod.Findings.Migrate, dir=root) == 0
    assert json.loads(capsys.readouterr().out)["stripped"] == []
