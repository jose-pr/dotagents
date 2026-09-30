"""`dotagents._ignore` reads .gitignore / .ignore as git does. The expected
set was recorded from `git check-ignore --no-index` on the same tree (28
paths, no mismatch); the test itself needs no git."""
from pathlib import Path

from dotagents._ignore import IgnoreRules, parse

ROOT_RULES = (
    "# comment\n*.log\n!keep.log\n/build\ndocs/**/draft*\ncache/\n**/tmp/x\nfoo/bar\n"
    "a?.txt\n[bc]at.md\n\\#hash\nspace\\ \n"
)
SUB_RULES = "*.md\n!README.md\n/local\n"
PATHS = [
    "x.log", "keep.log", "sub/y.log", "build", "build/out.o", "sub/build", "docs/a/b/draft1.md", "docs/draft2.md",
    "cache/z", "sub/cache/z", "cache", "q/tmp/x", "tmp/x", "foo/bar", "sub/foo/bar", "ab.txt", "abc.txt",
    "bat.md", "cat.md", "dat.md", "#hash", "space ", "sub/notes.md", "sub/README.md", "sub/local", "sub/deep/local",
    "sub/deep/README.md", "plain.txt",
]
GIT_IGNORES = {
    "#hash", "ab.txt", "bat.md", "build", "build/out.o", "cache", "cache/z", "cat.md", "docs/a/b/draft1.md",
    "docs/draft2.md", "foo/bar", "q/tmp/x", "space ", "sub/cache/z", "sub/local", "sub/notes.md", "sub/y.log",
    "tmp/x", "x.log",
}


def _tree(root: Path) -> None:
    (root / ".gitignore").write_text(ROOT_RULES, encoding="utf-8")
    (root / "sub").mkdir()
    (root / "sub" / ".gitignore").write_text(SUB_RULES, encoding="utf-8")
    for rel in PATHS:
        if rel in ("build", "cache"):
            (root / rel).mkdir(exist_ok=True)
            continue
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        if not (root / rel).exists():
            (root / rel).write_text("x", encoding="utf-8")


def test_matches_what_git_ignores(tmp_path):
    _tree(tmp_path)
    rules = IgnoreRules.from_tree(tmp_path, {".git"})
    got = {rel for rel in PATHS if rules.ignored(rel, (tmp_path / rel).is_dir())}
    assert got == GIT_IGNORES


def test_dot_ignore_is_read_after_gitignore_in_its_directory(tmp_path):
    (tmp_path / ".gitignore").write_text("*.db\n", encoding="utf-8")
    (tmp_path / ".ignore").write_text("!keep.db\nextra/\n", encoding="utf-8")
    rules = IgnoreRules.from_tree(tmp_path)
    assert rules.ignored("x.db") and not rules.ignored("keep.db"), ".ignore wins in its directory"
    assert rules.ignored("extra/file") and rules.ignored("extra", True)


def test_a_file_under_an_ignored_directory_stays_ignored():
    rules = IgnoreRules(parse("state/\n!state/keep\n"))
    assert rules.ignored("state/keep"), "as in git: no re-including below an ignored directory"


def test_skip_parts_are_not_walked(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / ".gitignore").write_text("*\n", encoding="utf-8")
    assert not IgnoreRules.from_tree(tmp_path, {".git"}).ignored("anything")
