"""An overlay's ``OVERLAY.md``: its front matter is the manifest, its body the context in place
of ``CONTEXT.md``. The same overlay in the ``overlay.cfg`` + ``CONTEXT.md`` form applies the same;
two manifests, a manifest outside the restricted YAML, and a body beside ``CONTEXT.md`` are
refused or reported.

tmp dirs only; the conftest isolates the home and the stores.
"""

import json
import logging
import re
from pathlib import Path

import pytest

from dotagents import _agents, _context, _env, _overlays
from dotagents._overlays import Overlay
from dotagents._scope import Scope
from dotagents.cli import OverlayAdd, OverlayShow

from _helpers import run_cmd as _run

BASE_AGENTS = "<!-- dotagents:begin -->\n# BASE\n## Load on demand\n<!-- dotagents:end -->\n"

#: The same overlay in both forms: its manifest, its rule and its context.
CFG = ('name = "docs"\ndescription = "Notes for the docs"\npriority = 300\n'
       '[env]\nGREETING = "hello"\nMODE = { value = "kept", if_unset = true }\n'
       '[[paths]]\nvariable = "DOCS_PATH"\npath = "share"\n')
FRONT = ('name: docs\ndescription: Notes for the docs\npriority: 300\n'
         'env:\n  GREETING: hello\n  MODE:\n    value: kept\n    if_unset: true\n'
         'paths:\n  - variable: DOCS_PATH\n    path: share\n')
CONTEXT = "Read the notes in the docs folder first.\n"
RULE = "- **Notes**: keep them short.\n"


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(str(path), "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    return path


def _md(front: str, body: str = CONTEXT, fence: str = "---") -> str:
    return "%s\n%s%s\n%s" % (fence, front, fence, body)


def _cfg_form(root: Path) -> Path:
    _write(root / "overlay.cfg", CFG)
    _write(root / "CONTEXT.md", CONTEXT)
    _write(root / "rules" / "short.md", RULE)
    _write(root / "share" / "note.txt", "x")
    return root


def _md_form(root: Path, front: str = FRONT) -> Path:
    _write(root / "OVERLAY.md", _md(front))
    _write(root / "rules" / "short.md", RULE)
    _write(root / "share" / "note.txt", "x")
    return root


@pytest.fixture(autouse=True)
def fresh_warnings(monkeypatch):
    monkeypatch.setattr(_overlays, "_warned", set())


def _store(tmp_path: Path, name: str) -> Path:
    store = tmp_path / name
    _write(store / "AGENTS.md", BASE_AGENTS)
    return store


def _install(src_root: Path, store: Path) -> None:
    code = _run(OverlayAdd, name=["docs"], repo=[str(src_root)], global_scope=True, agents_dir=store,
                copy=True, dry_run=False)
    assert code == 0


def _seen(store: Path) -> "tuple[dict, str, Overlay]":
    """What an installed ``docs`` overlay gives: the session environment, the context text with
    its provenance comments removed, and the overlay."""
    scope = Scope("user", store)
    env = _env.get_environment(scope, base_env={"PATH": ""}, logger=logging.getLogger("t"))
    text = _context.assemble_context(_agents.GeminiAgent(), Scope.of(
        agents_dir=store, project_root=store.parent / "proj", global_scope=True))
    text = re.sub(r"(?m)^<!-- Source:.*?-->\s*$", "", text).replace(store.as_posix(), "STORE")
    return env, text, Overlay(store / "overlays" / "docs")


# --------------------------------------------------------------------------
# The same overlay in both forms applies the same.
# --------------------------------------------------------------------------

def test_an_overlay_md_overlay_applies_like_the_overlay_cfg_form(tmp_path):
    for repo, build in (("cfg", _cfg_form), ("md", _md_form)):
        build(tmp_path / repo / "docs")
        _install(tmp_path / repo, _store(tmp_path, repo + "_store"))
    cfg_env, cfg_text, cfg = _seen(tmp_path / "cfg_store")
    md_env, md_text, md = _seen(tmp_path / "md_store")
    assert cfg.manifest_path.name == "overlay.cfg" and md.manifest_path.name == "OVERLAY.md"
    assert md.document() == cfg.document() and md.document()["priority"] == 300
    assert md.read_manifest()["description"] == "Notes for the docs"
    assert md.rules() == cfg.rules() == ["rules/short.md"]
    assert md_env["GREETING"] == cfg_env["GREETING"] == "hello" and md_env["MODE"] == "kept"
    assert md_env["DOCS_PATH"].endswith("share") and cfg_env["DOCS_PATH"].endswith("share")
    assert cfg.context_files() == ["CONTEXT.md"] and md.context_files() == ["OVERLAY.md"]
    assert CONTEXT.strip() in cfg_text and md_text == cfg_text
    assert "name: docs" not in md_text, "the front matter is not context"


def test_the_same_overlay_with_toml_front_matter(tmp_path):
    root = tmp_path / "docs"
    _write(root / "OVERLAY.md", "---\n#!toml\n%s---\n%s" % (CFG, CONTEXT))
    overlay = Overlay(root)
    assert overlay.manifest_problems() == []
    assert overlay.document()["env"]["GREETING"] == "hello"
    assert overlay.context_files() == ["OVERLAY.md"]


def test_a_body_with_no_front_matter_is_context_of_an_overlay_with_no_manifest(tmp_path):
    overlay = Overlay(_write(tmp_path / "docs" / "OVERLAY.md", "Just the notes.\n").parent)
    assert overlay.document() == {} and overlay.manifest_problems() == []
    assert overlay.context_files() == ["OVERLAY.md"]


def test_front_matter_with_no_body_leaves_context_md_the_context(tmp_path):
    root = tmp_path / "docs"
    _write(root / "OVERLAY.md", "---\nname: docs\n---\n  \n")
    _write(root / "CONTEXT.md", CONTEXT)
    overlay = Overlay(root)
    assert overlay.context_files() == ["CONTEXT.md"] and overlay.context_ignored() == []


def test_a_changed_overlay_md_is_read_again(tmp_path):
    root = tmp_path / "docs"
    _write(root / "OVERLAY.md", _md("priority: 100\n"))
    assert Overlay(root).document()["priority"] == 100
    # A different size: the cache is keyed on modification time and size, and two writes in one
    # clock tick share a time.
    _write(root / "OVERLAY.md", _md("priority: 2500\n"))
    assert Overlay(root).document()["priority"] == 2500


# --------------------------------------------------------------------------
# CONTEXT.md beside a body is ignored and reported.
# --------------------------------------------------------------------------

def test_context_md_beside_a_body_is_ignored_and_reported(tmp_path, caplog):
    store = _store(tmp_path, "store")
    root = _md_form(store / "overlays" / "docs")
    _write(root / "CONTEXT.md", "OLD-CONTEXT-TEXT\n")
    overlay = Overlay(root)
    with caplog.at_level(logging.WARNING):
        assert overlay.document()["name"] == "docs"
    assert "CONTEXT.md is ignored: the body of OVERLAY.md is its context" in caplog.text
    assert overlay.context_files() == ["OVERLAY.md"] and overlay.context_ignored() == ["CONTEXT.md"]
    _env_text = _context.assemble_context(_agents.GeminiAgent(), Scope.of(
        agents_dir=store, project_root=tmp_path / "proj", global_scope=True))
    assert CONTEXT.strip() in _env_text and "OLD-CONTEXT-TEXT" not in _env_text


def test_naming_context_md_does_not_bring_it_back(tmp_path):
    root = tmp_path / "docs"
    _write(root / "OVERLAY.md", _md("context:\n  - CONTEXT.md\n  - extra.md\n"))
    _write(root / "CONTEXT.md", "OLD\n")
    _write(root / "extra.md", "EXTRA\n")
    overlay = Overlay(root)
    assert overlay.context_files() == ["OVERLAY.md", "extra.md"]
    assert overlay.context_ignored() == ["CONTEXT.md"]


def test_a_harness_hint_naming_context_md_does_not_deliver_it_either(tmp_path):
    root = tmp_path / "docs"
    _write(root / "OVERLAY.md", _md("harness:\n  gemini-cli:\n    context:\n      - CONTEXT.md\n      - g.md\n"))
    _write(root / "CONTEXT.md", "OLD\n")
    overlay = Overlay(root)
    assert overlay.context_files("gemini-cli") == ["OVERLAY.md", "g.md"]
    assert overlay.context_files("codex") == ["OVERLAY.md"]


def test_no_default_context_leaves_the_body_out(tmp_path):
    root = tmp_path / "docs"
    _write(root / "OVERLAY.md", _md("defaults:\n  context: false\n"))
    assert Overlay(root).context_files() == []


def test_overlays_show_reports_the_ignored_file(tmp_path, capsys):
    store = _store(tmp_path, "store")
    root = _md_form(store / "overlays" / "docs")
    _write(root / "CONTEXT.md", "OLD\n")
    assert _run(OverlayShow, name="docs", global_scope=True, agents_dir=store, json=True) == 0
    info = json.loads(capsys.readouterr().out)
    assert info["context"] == ["OVERLAY.md"] and info["context_ignored"] == ["CONTEXT.md"]


# --------------------------------------------------------------------------
# Refusals.
# --------------------------------------------------------------------------

def _refused(root: Path) -> "list[str]":
    overlay = Overlay(root)
    problems = overlay.manifest_problems()
    assert problems, "the manifest was read"
    assert overlay.document() == {}, "none of its declarations apply"
    return problems


def test_both_manifests_are_refused(tmp_path, caplog):
    root = _cfg_form(tmp_path / "docs")
    _write(root / "OVERLAY.md", _md(FRONT))
    with caplog.at_level(logging.WARNING):
        problems = _refused(root)
    assert "two-manifests" in "; ".join(problems) and "its manifest is ignored" in caplog.text
    assert "OVERLAY.md" not in Overlay(root).context_files(), "a refused body is not delivered"


def test_overlays_add_refuses_both_manifests(tmp_path):
    root = _cfg_form(tmp_path / "src" / "docs")
    _write(root / "OVERLAY.md", _md(FRONT))
    with pytest.raises(SystemExit) as raised:
        _install(tmp_path / "src", _store(tmp_path, "store"))
    assert "two-manifests" in str(raised.value)


@pytest.mark.parametrize("front,word", [
    ("name: &keep docs\n", "yaml-anchor"),
    ("name: docs\nother: *keep\n", "yaml-anchor"),
    ("name: !!str docs\n", "yaml-tag"),
    ("base: &b {priority: 1}\n<<: *b\n", "yaml"),
    ("- name\n- docs\n", "not-a-mapping"),
    ("name: docs\nname: other\n", "duplicate-key"),
    ("name: null\n", "null-value"),
    ("#!ini\nname = docs\n", "unsupported-format"),
])
def test_yaml_outside_the_restricted_subset_is_refused(tmp_path, front, word):
    root = tmp_path / "docs"
    _write(root / "OVERLAY.md", _md(front))
    problems = "; ".join(_refused(root))
    assert word in problems, problems


def test_an_unclosed_fence_is_refused(tmp_path):
    root = tmp_path / "docs"
    _write(root / "OVERLAY.md", "---\nname: docs\nthe notes\n")
    assert "unterminated" in "; ".join(_refused(root))


def test_a_manifest_that_fails_validation_is_refused(tmp_path):
    root = tmp_path / "docs"
    _write(root / "OVERLAY.md", _md("priority: soon\n"))
    assert "/priority" in "; ".join(_refused(root))


def test_a_body_that_is_context_and_not_utf8_is_refused(tmp_path):
    root = tmp_path / "docs"
    root.mkdir()
    (root / "OVERLAY.md").write_bytes(b"---\nname: docs\n---\n\xff\xfe notes\n")
    assert "invalid-utf8" in "; ".join(_refused(root))
