"""The vendored ``coqui_aos`` is the recorded copy, unedited, and is the one dotagents imports."""

import hashlib
import json
import sys
from pathlib import Path

from dotagents import _aos
from dotagents._vendor import coqui_aos

DIRECTORY = Path(__file__).resolve().parents[1] / "src" / "dotagents" / "_vendor" / "coqui_aos"


def test_the_files_are_the_recorded_ones():
    record = json.loads((DIRECTORY / "VENDORED.json").read_text(encoding="utf-8"))
    have = {
        path.name: "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
        for path in DIRECTORY.iterdir()
        if path.is_file() and path.name not in ("VENDORED.json", "VENDORED.md")
    }
    assert have == record["files"], "edit nothing here: tools/vendor_coqui_aos.py copies a commit"
    assert len(record["commit"]) == 40 and (DIRECTORY / "LICENSE").is_file()


def test_dotagents_uses_the_vendored_copy():
    assert Path(coqui_aos.__file__).parent == DIRECTORY
    assert _aos.coqui_aos is coqui_aos
    assert "coqui_aos" not in sys.modules, "no top-level coqui_aos is imported beside it"
