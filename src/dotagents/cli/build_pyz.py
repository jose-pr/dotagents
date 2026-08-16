"""`dotagents build-pyz` -- vendor deps and package a self-contained pyz."""

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from duho import Cmd, LoggingArgs

#: `pyproject.toml`'s `[project] version = "..."` line -- deliberately a plain
#: regex, not a TOML parser (tomllib is 3.11+, this repo's floor is 3.9, and a
#: single quoted scalar under a known table header doesn't need one).
_PYPROJECT_VERSION_RE = re.compile(r'(?m)^version\s*=\s*"([^"]+)"')


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

    def __call__(self) -> int:
        import zipapp

        # This module lives at src/dotagents/cli/build_pyz.py, so the repo root
        # is parents[3] (cli -> dotagents -> src -> repo) and the dotagents
        # package dir is parents[1].
        #
        # The repo's `tools/` is NOT bundled. It used to ride along as
        # `dotagents/_tools` for compiled `audit`/`leak-check` wrappers that
        # shelled out to it; both wrappers are gone (audit is repo CI tooling,
        # leak-check is a personal command module), nothing reads `_tools`, and
        # `tools/audit.py`'s own docstring says it is not shipped in the .pyz --
        # which is only true now that this stopped copying it.
        repo_root = Path(__file__).resolve().parents[3]

        with tempfile.TemporaryDirectory(prefix="dotagents-pyz-") as tmp:
            stage = Path(tmp) / "stage"
            stage.mkdir()

            self._logger_.info(
                "vendoring duho==%s pathlib_next==%s via pip --target",
                self.duho_version,
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
                    "duho==%s" % self.duho_version,
                    "pathlib_next==%s" % self.pathlib_next_version,
                ]
            )
            if rc != 0:
                return rc

            dotagents_pkg_src = Path(__file__).resolve().parents[1]
            dotagents_pkg_dest = stage / "dotagents"
            shutil.copytree(
                dotagents_pkg_src,
                dotagents_pkg_dest,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )

            # `dotagents.__version__` is a second, independently-maintained copy
            # of pyproject.toml's `version` -- confirmed stale on a real release
            # (pyproject.toml had already been bumped twice past what
            # __version__ still said, so `dotagents --version` on a freshly
            # built pyz reported a version two releases old). Rather than trust
            # the source tree's __init__.py to have been bumped in lockstep,
            # read pyproject.toml directly and rewrite the STAGED copy's
            # __version__ to match -- the built artifact is then correct
            # regardless of whether __init__.py itself was ever touched.
            pyproject = repo_root / "pyproject.toml"
            match = _PYPROJECT_VERSION_RE.search(pyproject.read_text(encoding="utf-8"))
            if match is None:
                self._logger_.warning(
                    "could not read version from %s; __version__ left as-is", pyproject
                )
            else:
                version = match.group(1)
                init_py = dotagents_pkg_dest / "__init__.py"
                init_py.write_text(
                    '"""dotagents: installable CLI for the dotagents agent-config payload."""\n\n'
                    '__version__ = "%s"\n' % version,
                    encoding="utf-8",
                )
                self._logger_.info("stamped __version__ = %s (from pyproject.toml)", version)

            for path in stage.rglob("*.dist-info"):
                shutil.rmtree(path, ignore_errors=True)
            for path in stage.rglob("__pycache__"):
                shutil.rmtree(path, ignore_errors=True)
            for path in stage.rglob("tests"):
                if path.is_dir():
                    shutil.rmtree(path, ignore_errors=True)

            main_py = stage / "__main__.py"
            main_py.write_text(
                "from dotagents.cli import main\n\nraise SystemExit(main())\n",
                encoding="utf-8",
            )

            out_path = Path(self.out)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            zipapp.create_archive(str(stage), target=str(out_path), interpreter=self.python)
            self._logger_.info("built %s", out_path)

        return 0
