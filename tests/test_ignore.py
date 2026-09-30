"""`dotagents._ignore` reads the .gitignore / .ignore at a directory's ROOT
as git reads a .gitignore; ignore files in subdirectories are ordinary files.
The expected set was recorded from `git check-ignore --no-index` with the
same root .gitignore (26 paths, no mismatch); the test needs no git."""
from pathlib import Path

from dotagents._ignore import IgnoreRules, parse

ROOT_RULES = (
    "# comment\n*.log\n!keep.log\n/build\ndocs/**/draft*\ncache/\n**/tmp/x\nfoo/bar\n"
    "a?.txt\n[bc]at.md\n\\#hash\nspace\\ \nsub/*.md\n!sub/README.md\n"
)
PATHS = [
    "x.log", "keep.log", "sub/y.log", "build", "build/out.o", "sub/build", "docs/a/b/draft1.md", "docs/draft2.md",
    "cache/z", "sub/cache/z", "cache", "q/tmp/x", "tmp/x", "foo/bar", "sub/foo/bar", "ab.txt", "abc.txt",
    "bat.md", "cat.md", "dat.md", "#hash", "space ", "sub/notes.md", "sub/README.md", "sub/deep/notes.md", "plain.txt",
]
GIT_IGNORES = {
    "#hash", "ab.txt", "bat.md", "build", "build/out.o", "cache", "cache/z", "cat.md", "docs/a/b/draft1.md",
    "docs/draft2.md", "foo/bar", "q/tmp/x", "space ", "sub/cache/z", "sub/notes.md", "sub/y.log", "tmp/x", "x.log",
}


def test_matches_what_git_ignores(tmp_path):
    (tmp_path / ".gitignore").write_text(ROOT_RULES, encoding="utf-8")
    for rel in PATHS:
        if rel in ("build", "cache"):
            (tmp_path / rel).mkdir(exist_ok=True)
            continue
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        if not (tmp_path / rel).exists():
            (tmp_path / rel).write_text("x", encoding="utf-8")
    rules = IgnoreRules.for_root(tmp_path)
    assert {rel for rel in PATHS if rules.ignored(rel, (tmp_path / rel).is_dir())} == GIT_IGNORES


def test_only_the_roots_files_count(tmp_path):
    """A .gitignore in a subdirectory is an ordinary file (the engineering
    overlay ships a references/.gitignore TEMPLATE); nothing above is read."""
    (tmp_path / "references").mkdir()
    (tmp_path / "references" / ".gitignore").write_text("*\n", encoding="utf-8")
    (tmp_path / "references" / ".ignore").write_text("*\n", encoding="utf-8")
    rules = IgnoreRules.for_root(tmp_path)
    assert not rules.ignored("references/README.md")
    assert not IgnoreRules.for_root(tmp_path / "missing").rules, "no files, no rules"


def test_dot_ignore_is_read_after_gitignore(tmp_path):
    (tmp_path / ".gitignore").write_text("*.db\n", encoding="utf-8")
    (tmp_path / ".ignore").write_text("!keep.db\nextra/\n", encoding="utf-8")
    rules = IgnoreRules.for_root(tmp_path)
    assert rules.ignored("x.db") and rules.ignored("lib/x.db") and not rules.ignored("keep.db"), ".ignore wins"
    assert rules.ignored("extra/file") and rules.ignored("extra", True)


def test_a_file_under_an_ignored_directory_stays_ignored():
    rules = IgnoreRules(parse("state/\n!state/keep\n"))
    assert rules.ignored("state/keep"), "as in git: no re-including below an ignored directory"
