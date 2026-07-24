"""`dotagents build-pyz` -- vendor deps and package a self-contained pyz."""

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

from duho import Cmd, LoggingArgs


class BuildPyz(LoggingArgs, Cmd):
    """Vendor duho/pathlib_next via pip --target and package a self-contained dotagents.pyz."""

    _parsername_ = "build-pyz"

    out: Path = Path("dist") / "dotagents.pyz"
    "Output path for the built pyz."
    ("--out",)

    python: str = "/usr/bin/env python3"
    "Shebang line to embed in the pyz."
    ("--python",)

    duho_version: str = "0.3.3"
    "Pinned duho version to vendor."
    ("--duho-version",)

    pathlib_next_version: str = "0.8.0"
    "Pinned pathlib_next version to vendor."
    ("--pathlib-next-version",)

    tools_dir: Optional[Path] = None
    "Repo tools/ dir (required tooling) to bundle as _tools (default: autodetected)."
    ("--tools-dir",)

    def __call__(self) -> int:
        import zipapp

        # This module lives at src/dotagents/cli/build_pyz.py, so the repo root
        # is parents[3] (cli -> dotagents -> src -> repo) and the dotagents
        # package dir is parents[1].
        repo_root = Path(__file__).resolve().parents[3]
        tools_src = Path(self.tools_dir) if self.tools_dir else (repo_root / "tools")
        if not tools_src.exists():
            raise SystemExit("error: repo tools/ not found at %s (pass --tools-dir)" % tools_src)

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

            tools_dest = dotagents_pkg_dest / "_tools"
            shutil.copytree(
                tools_src,
                tools_dest,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
            )

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
