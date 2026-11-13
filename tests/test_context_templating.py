"""An overlay's context names its own location and settings: ``${AOS_OVERLAY_ROOT}`` and the
options its manifest declares, replaced when the context is assembled (the specification's
Templating section), never in the installed file."""

from __future__ import annotations

import pytest

from dotagents import _agents, _context, _policy
from dotagents._scope import Scope

CONTEXT = (
    "root=${AOS_OVERLAY_ROOT}\n"
    "name=${AOS_OVERLAY_NAME} scope=${AOS_OVERLAY_SCOPE} project=${AOS_PROJECT_ROOT}\n"
    "depth=${depth} retries=${retries} token=${token}\n"
    "literal=$${AOS_OVERLAY_ROOT} and $${ZETA_OVERLAY_ROOT}\n"
    "unknown=${HOME} shell=${ZETA_OVERLAY_ROOT}/kb\n"
)


@pytest.fixture
def store(tmp_path, monkeypatch):
    agents = tmp_path / "agents"
    project = tmp_path / "proj"
    (project / ".agents").mkdir(parents=True)
    overlay = agents / "overlays" / "zeta"
    overlay.mkdir(parents=True)
    (overlay / "CONTEXT.md").write_text("---\nmeta: ${AOS_OVERLAY_NAME}\n---\n" + CONTEXT, encoding="utf-8")
    (overlay / "overlay.cfg").write_text(
        'name = "zeta"\ncontext = ["CONTEXT.md"]\n'
        '[options.depth]\ntype = "string"\ndefault = "normal"\n'
        '[options.retries]\ntype = "integer"\ndefault = 3\n'
        '[options.token]\ntype = "string"\nsensitive = true\n',
        encoding="utf-8",
    )
    (agents / "AGENTS.md").write_text("store ${AOS_OVERLAY_NAME} $${x}\n", encoding="utf-8")
    monkeypatch.chdir(project)  # a user scope's project is the working directory
    return agents, project, overlay


def assemble(agents, project, **kw):
    scope = Scope.of(agents_dir=agents, project_root=project, global_scope=True)
    return _context.assemble_context(_agents.GeminiAgent(), scope, **kw)


def test_a_context_file_reads_its_overlays_root_name_scope_and_options(store):
    agents, project, overlay = store
    text = assemble(agents, project)
    assert "root=%s\n" % overlay in text
    assert "name=zeta scope=user project=%s\n" % project in text
    assert "depth=normal retries=3 token=${token}\n" in text
    assert "meta:" not in text  # the front matter is dropped, not templated into the context


def test_an_escape_and_an_unknown_name_stay_as_written(store):
    agents, project, _overlay = store
    text = assemble(agents, project)
    assert "literal=${AOS_OVERLAY_ROOT} and ${ZETA_OVERLAY_ROOT}\n" in text
    assert "unknown=${HOME} shell=" in text
    # dotagents' own $<NAME>_OVERLAY_ROOT expansion is unchanged for a name the overlay did not escape
    assert "shell=%s/kb\n" % (agents / "overlays" / "zeta") in text


def test_only_an_overlays_context_is_templated(store):
    agents, project, _overlay = store
    assert "store ${AOS_OVERLAY_NAME} $${x}\n" in assemble(agents, project)


def test_a_configured_value_wins_and_a_sensitive_one_never_appears(store):
    agents, project, _overlay = store
    (agents / "dotagents.toml").write_text(
        '[overlays.zeta]\noptions = { depth = "deep", retries = 5, token = "s3cret" }\n', encoding="utf-8")
    text = assemble(agents, project)
    assert "depth=deep retries=5 token=${token}\n" in text
    assert "s3cret" not in text


def test_a_value_is_never_read_again(store):
    agents, project, _overlay = store
    (agents / "dotagents.toml").write_text(
        '[overlays.zeta]\noptions = { depth = "${AOS_OVERLAY_ROOT} ${ZETA_OVERLAY_ROOT}" }\n', encoding="utf-8")
    assert "depth=${AOS_OVERLAY_ROOT} ${ZETA_OVERLAY_ROOT} retries" in assemble(agents, project)


def test_a_value_of_the_wrong_type_is_refused(store):
    agents, project, _overlay = store
    (agents / "dotagents.toml").write_text('[overlays.zeta]\noptions = { retries = "many" }\n', encoding="utf-8")
    with pytest.raises(_policy.PolicyError) as raised:
        assemble(agents, project)
    assert "retries" in str(raised.value)


def test_text_for_a_file_keeps_machine_paths_and_settings(store):
    agents, project, overlay = store
    text = assemble(agents, project, expand_vars=False)
    assert "root=${AOS_OVERLAY_ROOT}\n" in text
    assert "project=${AOS_PROJECT_ROOT}\n" in text
    assert "depth=normal" in text
    assert "name=zeta scope=user" in text
    assert "literal=${AOS_OVERLAY_ROOT}" in text
    assert str(overlay) not in text.replace("<!-- Source: %s" % (overlay / "CONTEXT.md"), "")


def test_the_installed_file_is_unchanged(store):
    agents, project, overlay = store
    before = (overlay / "CONTEXT.md").read_bytes()
    assemble(agents, project)
    assert (overlay / "CONTEXT.md").read_bytes() == before
