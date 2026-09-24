"""`dotagents build-pyz` -- vendor deps and package a self-contained pyz."""

import fnmatch
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from duho import Cmd, LoggingArgs

from dotagents._fs import write_text_lf

#: The interpreter the vendored dependencies are resolved for: the floor of
#: pyproject.toml's `requires-python`. pip picks pure-Python wheels only
#: (`--platform any --implementation py`), so the zipapp runs on any
#: interpreter from this one up and never carries a native module zipimport
#: could not load, or one built for the builder's OS and architecture.
_VENDOR_PYTHON = "3.9"

#: Names never copied into the .pyz: caches, plus the private files the wheel
#: and sdist exclude too (pyproject.toml) -- `*.local.*` is the unshared
#: per-machine override convention and `CLAUDE*` is agent config. Matched
#: case-sensitively, so `CLAUDE*` cannot catch a module named `claude*.py` on a
#: case-insensitive filesystem (`shutil.ignore_patterns` folds case on Windows).
_UNSHIPPED_PATTERNS = ("__pycache__", "*.pyc", "*.local.*", "CLAUDE*")


def _ignore_unshipped(_directory: str, names: "list[str]") -> "set[str]":
    """``shutil.copytree`` ignore callback for :data:`_UNSHIPPED_PATTERNS`."""
    return {
        name
        for name in names
        if any(fnmatch.fnmatchcase(name, pattern) for pattern in _UNSHIPPED_PATTERNS)
    }


def _dist_info_name_version(dist_info: Path) -> "dict[str, str]":
    """``{Name: Version}`` from a ``*.dist-info/METADATA``, or ``{}``."""
    metadata = dist_info / "METADATA"
    if not metadata.is_file():
        return {}
    name = version = None
    for line in metadata.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("Name:"):
            name = line[5:].strip()
        elif line.startswith("Version:"):
            version = line[8:].strip()
        if name and version:
            break
        if not line.strip():
            break  # the header ends at the first blank line
    return {name: version} if name and version else {}


class BuildPyz(LoggingArgs, Cmd):
    """Vendor duho/pathlib_next via pip --target and package a self-contained dotagents.pyz."""

    _parsername_ = "build-pyz"

    out: Path = Path("dist") / "dotagents.pyz"
    "Output path for the built pyz."
    ("--out",)

    python: str = "/usr/bin/env python3"
    "Shebang line to embed in the pyz."
    ("--python",)

    # These two are a SECOND copy of the dependency versions declared in
    # `pyproject.toml`'s `[project] dependencies`, and the two must move
    # together: the zipapp bundles what the package claims to support, so a
    # stale pin here ships an artifact `pip install dotagents-cli` would refuse.
    # Pin the FLOOR of each declared range, not the latest patch -- the .pyz then
    # exercises the minimum the metadata promises.
    duho_version: str = "0.5.0"
    "Pinned duho version to vendor."
    ("--duho-version",)

    pathlib_next_version: str = "0.9.0"
    "Pinned pathlib_next version to vendor."
    ("--pathlib-next-version",)

    extras: str = ""
    (
        "pathlib_next extras to vendor too, comma-separated (uri, http, s3), "
        "so the .pyz speaks those schemes for overlay sources; none by default. "
        "Only pure-Python dependencies can be vendored, so sftp (cryptography, "
        "bcrypt, pynacl) cannot."
    )
    ("--extras",)

    def __call__(self) -> int:
        import zipapp

        # This module lives at src/dotagents/cli/build_pyz.py, so the repo root
        # is parents[3] (cli -> dotagents -> src -> repo) and the dotagents
        # package dir is parents[1]. Only the package is bundled; the repo's
        # `tools/` is CI tooling and never ships in the .pyz.
        repo_root = Path(__file__).resolve().parents[3]
        pyproject = repo_root / "pyproject.toml"
        dotagents_pkg_src = Path(__file__).resolve().parents[1]
        if not pyproject.is_file() or not dotagents_pkg_src.is_dir():
            # A wheel install (site-packages) or a run from inside a .pyz has
            # no checkout around it -- say so instead of a FileNotFoundError.
            raise SystemExit(
                "error: build-pyz needs a source checkout (no pyproject.toml above "
                "%s); run it from the dotagents repository" % dotagents_pkg_src
            )

        with tempfile.TemporaryDirectory(prefix="dotagents-pyz-") as tmp:
            stage = Path(tmp) / "stage"
            stage.mkdir()

            extras = ",".join(e.strip() for e in self.extras.split(",") if e.strip())
            extras_spec = "[%s]" % extras if extras else ""
            self._logger_.info(
                "vendoring duho==%s pathlib_next%s==%s via pip --target",
                self.duho_version,
                extras_spec,
                self.pathlib_next_version,
            )
            rc = subprocess.call(
                [
                    sys.executable,
                    "-m",
                    "pip",
                    "install",
                    "--target",
                    str(stage),
                    # Pure-Python wheels resolved for the oldest supported
                    # interpreter, whatever this build runs on (_VENDOR_PYTHON).
                    "--only-binary=:all:",
                    "--platform",
                    "any",
                    "--implementation",
                    "py",
                    "--python-version",
                    _VENDOR_PYTHON,
                    "duho==%s" % self.duho_version,
                    "pathlib_next%s==%s" % (extras_spec, self.pathlib_next_version),
                ]
            )
            if rc != 0:
                if extras:
                    self._logger_.error(
                        "pip could not vendor pathlib_next%s; an extra whose "
                        "dependencies ship only native wheels (sftp) cannot go "
                        "into a zipapp",
                        extras_spec,
                    )
                return rc
            # pip's console-script launchers: native executables for the build
            # machine that embed its interpreter path. A zipapp never runs them.
            shutil.rmtree(stage / "bin", ignore_errors=True)

            # The package is copied as-is: `__version__` in `__init__.py` is
            # the single source of the version (pyproject.toml reads it too).
            dotagents_pkg_dest = stage / "dotagents"
            shutil.copytree(dotagents_pkg_src, dotagents_pkg_dest, ignore=_ignore_unshipped)

            # What went into the bundle, for `dotagents about`: the zipapp
            # carries no dist-info (stripped below), so record the vendored
            # distributions' names and versions beside the package first.
            bundled = {}
            for path in sorted(stage.rglob("*.dist-info")):
                bundled.update(_dist_info_name_version(path))
            # Generated files are written LF, like every copied source, so the
            # artifact's bytes do not depend on the OS it was built on.
            write_text_lf(
                dotagents_pkg_dest / "_bundle.json",
                json.dumps({
                    "packages": bundled,
                    "extras": extras,
                    "python": "%d.%d.%d" % sys.version_info[:3],
                }, indent=2) + "\n",
            )
            for path in stage.rglob("*.dist-info"):
                shutil.rmtree(path, ignore_errors=True)
            for path in stage.rglob("__pycache__"):
                shutil.rmtree(path, ignore_errors=True)
            for path in stage.rglob("tests"):
                if path.is_dir():
                    shutil.rmtree(path, ignore_errors=True)

            write_text_lf(
                stage / "__main__.py",
                "from dotagents.cli import main\n\nraise SystemExit(main())\n",
            )

            out_path = Path(self.out)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            zipapp.create_archive(
                str(stage), target=str(out_path), interpreter=self.python, compressed=True
            )
            self._logger_.info("built %s", out_path)

        return 0
