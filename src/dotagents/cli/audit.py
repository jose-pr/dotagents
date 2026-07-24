"""`dotagents audit` -- run the config auditor against a payload/install dest."""

import subprocess
import sys
from pathlib import Path
from typing import Optional

from duho import Cmd, LoggingArgs

from dotagents.cli._common import _package_data_dir


class Audit(LoggingArgs, Cmd):
    """Run the config auditor against a payload/install destination."""

    _parsername_ = "audit"

    root: Path = Path.home() / ".agents"
    "Root directory to audit (an installed dest, or a repo payload/ dir)."
    ("--root",)

    check_templates: bool = False
    "Also run --check-templates (needs Python 3.11+)."
    ("--check-templates",)

    repo_hygiene: Optional[Path] = None
    "Also run --repo-hygiene against the given repo root."
    ("--repo-hygiene",)

    def __call__(self) -> int:
        auditor_path = Path(self.root) / "tools" / "audit_config.py"
        if not auditor_path.exists():
            # Fall back to the auditor bundled with this package (required
            # tooling, shipped as package data `_tools`), then to a repo
            # checkout's top-level tools/ (dev use).
            bundled_tools = _package_data_dir("_tools")
            if bundled_tools is not None and (bundled_tools / "audit_config.py").exists():
                auditor_path = bundled_tools / "audit_config.py"
            else:
                # This module lives at src/dotagents/cli/audit.py, so the repo
                # root is parents[3] (cli -> dotagents -> src -> repo).
                repo_tool = Path(__file__).resolve().parents[3] / "tools" / "audit_config.py"
                if repo_tool.exists():
                    auditor_path = repo_tool
        if not auditor_path.exists():
            raise SystemExit("error: could not find audit_config.py under %s" % self.root)

        rc = 0
        cmd = [sys.executable, str(auditor_path), "--root", str(self.root)]
        rc |= subprocess.call(cmd)
        if self.check_templates:
            rc |= subprocess.call(cmd + ["--check-templates"])
        if self.repo_hygiene:
            rc |= subprocess.call(
                [sys.executable, str(auditor_path), "--repo-hygiene", str(self.repo_hygiene)]
            )
        return rc
