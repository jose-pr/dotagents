"""The shipped API header, ``src/dotagents/AGENTS.md``, covers the public API.

A consuming agent reads that file instead of the source, so a public name that
is missing from it is invisible. Every ``dotagents._*`` helper module and every
``dotagents.cli`` module has a section (a ``## `` heading naming the module in
backticks), and that section names each public function, class, constant and
public method the module defines. The names are read from the source with
``ast``, so nothing is imported and a new module or name cannot slip past.
"""

import ast
import re
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1] / "src" / "dotagents"
HEADER = PKG / "AGENTS.md"


def _documented_modules() -> "dict[str, Path]":
    """``{dotted module name: source file}`` for every module the header covers."""
    modules = {}
    for path in sorted(PKG.glob("_*.py")):
        if path.name in ("__init__.py", "__main__.py"):
            continue
        modules["dotagents." + path.stem] = path
    for path in sorted((PKG / "cli").glob("*.py")):
        name = "dotagents.cli" if path.name == "__init__.py" else "dotagents.cli." + path.stem
        modules[name] = path
    return modules


def _public_names(path: Path) -> "list[str]":
    """Public names the module DEFINES (imports excluded): top-level functions,
    classes and assigned constants, plus each public class's public methods."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = []

    def add(name: str) -> None:
        if not name.startswith("_") and name not in names:
            names.append(name)

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            add(node.name)
        elif isinstance(node, ast.ClassDef):
            add(node.name)
            if node.name.startswith("_"):
                continue
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    add(item.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            add(node.target.id)
    return names


def _sections() -> "list[tuple[str, str]]":
    """``(heading, body)`` for each ``## `` section of the header."""
    text = HEADER.read_text(encoding="utf-8")
    parts = re.split(r"(?m)^## ", text)[1:]
    out = []
    for part in parts:
        heading, _, body = part.partition("\n")
        out.append((heading, body))
    return out


def _section_for(module: str) -> "str | None":
    for heading, body in _sections():
        if "`%s`" % module in heading:
            return body
    return None


MODULES = _documented_modules()


def test_the_header_ships_inside_the_package():
    assert HEADER.is_file()


@pytest.mark.parametrize("module", sorted(MODULES))
def test_every_module_has_a_section(module):
    assert _section_for(module) is not None, (
        "src/dotagents/AGENTS.md has no '## ... `%s`' section" % module
    )


@pytest.mark.parametrize("module", sorted(MODULES))
def test_every_public_name_is_in_its_modules_section(module):
    section = _section_for(module)
    if section is None:
        pytest.skip("no section (reported by test_every_module_has_a_section)")
    missing = [
        name for name in _public_names(MODULES[module])
        if not re.search(r"(?<![\w])%s(?![\w])" % re.escape(name), section)
    ]
    assert not missing, "%s: not in its AGENTS.md section: %s" % (module, ", ".join(missing))


def test_the_header_has_no_repository_relative_links():
    # An installed copy has no repository around it: every link is absolute.
    text = HEADER.read_text(encoding="utf-8")
    relative = [
        target for target in re.findall(r"\]\(([^)]+)\)", text)
        if not re.match(r"[a-z][a-z0-9+.-]*://", target)
    ]
    assert not relative, relative
