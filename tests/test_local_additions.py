"""The user's own additions to installed overlay files: a store file at the
same relative path as an overlay's (``~/.agents/kb/RUST.md`` for the rust
overlay's ``kb/RUST.md``). `overlays sync` replaces the overlay's copy, so this
is where the user's text lives; `dotagents context` lists what it finds, and
the base AGENTS.md tells every agent to read them."""
import json

from dotagents import _agents, _context
from dotagents._resources import BASE_PROJECT_TEMPLATE, BASE_ROOT, base_agents_text
from dotagents._scope import Scope


def _write(path, text="x\n"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _world(tmp_path):
    user = tmp_path / "user"
    project = tmp_path / "proj"
    rust = user / "overlays" / "rust"
    _write(rust / "overlay.toml", 'name = "rust"\n')
    _write(rust / "CONTEXT.md", "RUST-CONTEXT\n")
    _write(rust / "kb" / "RUST.md", "upstream rust\n")
    _write(rust / "kb" / "NOTES.md", "no addition for this one\n")
    _write(rust / "flows" / "deep" / "STEP.md", "upstream step\n")
    _write(rust / "skills" / "rs" / "SKILL.md", "---\nname: rs\ndescription: d\n---\n")
    _write(rust / "kb" / "data.json", "{}\n")
    _write(rust / "AGENTS.md", "an overlay-root file\n")
    _write(user / "AGENTS.md", "# user\n")
    _write(user / "kb" / "RUST.md", "my own note\n")
    _write(user / "flows" / "deep" / "STEP.md", "my step\n")
    _write(user / "skills" / "rs" / "SKILL.md", "---\nname: rs\ndescription: d\n---\n")  # a published copy
    _write(user / "kb" / "data.json", "{}\n")
    _write(project / ".agents" / "kb" / "RUST.md", "a project note\n")
    return user, project


def _scope(user, project, global_scope=False):
    return Scope.of(agents_dir=user, project_root=project, global_scope=global_scope)


def test_additions_are_matched_by_relative_path_in_every_store(tmp_path):
    user, project = _world(tmp_path)
    found = {p.relative_to(user / "overlays" / "rust").as_posix(): [q for q in qs]
             for p, qs in _context.local_additions(_scope(user, project))}
    assert sorted(found) == ["flows/deep/STEP.md", "kb/RUST.md"], (
        "only .md files in a subdirectory, never skills/ (published copies) or the root"
    )
    assert found["kb/RUST.md"] == [user / "kb" / "RUST.md", project / ".agents" / "kb" / "RUST.md"]
    user_only = {p.name for p, _ in _context.local_additions(_scope(user, project, True))}
    assert user_only == {"RUST.md", "STEP.md"}


def test_context_lists_them_and_json_carries_them(tmp_path):
    user, project = _world(tmp_path)
    codex = _agents.CodexAgent()
    text = _context.assemble_context(codex, _scope(user, project))
    assert "## Local additions to overlay files" in text
    assert str(user / "kb" / "RUST.md") in text and str(project / ".agents" / "kb" / "RUST.md") in text
    assert "my own note" not in text, "listed, not inlined"
    data = _context.assemble_context_data(codex, _scope(user, project))
    assert "Local additions" not in data["context"]
    entries = {e["overlay_file"]: e["local"] for e in data["local_additions"]}
    assert entries[str(user / "overlays" / "rust" / "kb" / "RUST.md")] == [
        str(user / "kb" / "RUST.md"), str(project / ".agents" / "kb" / "RUST.md"),
    ]
    json.dumps(data)


def test_no_section_without_additions(tmp_path):
    user = tmp_path / "user"
    _write(user / "overlays" / "rust" / "overlay.toml", 'name = "rust"\n')
    _write(user / "overlays" / "rust" / "CONTEXT.md", "RUST-CONTEXT\n")
    _write(user / "overlays" / "rust" / "kb" / "RUST.md", "upstream\n")
    _write(user / "AGENTS.md", "# user\n")
    text = _context.assemble_context(_agents.CodexAgent(), Scope.of(agents_dir=user, global_scope=True))
    assert "RUST-CONTEXT" in text and "Local additions" not in text


def test_the_base_rule_is_in_the_user_block_only(tmp_path):
    user_block = base_agents_text(BASE_ROOT, tmp_path)
    assert "**Local additions to overlay files**" in user_block
    assert "`dotagents context` lists the ones it finds" in user_block
    project = (BASE_ROOT / BASE_PROJECT_TEMPLATE).read_text(encoding="utf-8")
    assert "Local additions" not in project, "a project session reads the user block too"
