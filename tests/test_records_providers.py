"""Provider selection of `dotagents._records`: markdown by default, a named tracker recognised and validated, and a
tracker's operations refused with a message that says dotagents has no adapter for them."""

from pathlib import Path

import pytest

from dotagents._records import (
    FolderStore,
    GitHubProvider,
    MarkdownProvider,
    load_provider_settings,
    open_provider,
)


def _store(tmp_path: Path) -> FolderStore:
    root = tmp_path / "findings"
    root.mkdir()
    return FolderStore(root, status_folders={"active": "active", "processed": "processed"})


def test_markdown_is_the_default(tmp_path):
    store = _store(tmp_path)
    provider = open_provider(store, tmp_path, scope_key="findings")
    assert isinstance(provider, MarkdownProvider)
    assert provider.capabilities()["kind"] == "markdown"
    assert load_provider_settings(tmp_path, "findings") == {"provider": "markdown"}


def test_a_scope_section_selects_a_tracker(tmp_path):
    (tmp_path / "records.toml").write_text('[findings]\nprovider = "github"\nrepo = "o/r"\n', encoding="utf-8")
    provider = open_provider(_store(tmp_path), tmp_path, scope_key="findings")
    assert isinstance(provider, GitHubProvider)
    caps = provider.capabilities()
    assert caps["kind"] == "github" and caps["configured"] is True


def test_an_unknown_provider_names_the_known_ones(tmp_path):
    (tmp_path / "records.toml").write_text('[findings]\nprovider = "nope"\n', encoding="utf-8")
    with pytest.raises(SystemExit) as stopped:
        open_provider(_store(tmp_path), tmp_path, scope_key="findings")
    assert "unknown record provider" in str(stopped.value) and "github" in str(stopped.value)


def test_a_tracker_without_its_keys_says_which(tmp_path):
    (tmp_path / "records.toml").write_text('[findings]\nprovider = "jira"\nproject = "P"\n', encoding="utf-8")
    provider = open_provider(_store(tmp_path), tmp_path, scope_key="findings")
    assert provider.capabilities()["configured"] is False
    with pytest.raises(SystemExit) as stopped:
        provider.list_records()
    assert "url" in str(stopped.value)


def test_a_configured_tracker_has_no_adapter_here(tmp_path):
    (tmp_path / "records.toml").write_text('[findings]\nprovider = "github"\nrepo = "o/r"\n', encoding="utf-8")
    provider = open_provider(_store(tmp_path), tmp_path, scope_key="findings")
    with pytest.raises(SystemExit) as stopped:
        provider.create("x", meta={"description": "d"})
    assert "adapter" in str(stopped.value)
