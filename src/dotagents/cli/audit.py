"""`dotagents audit` -- run the config auditor against a payload/install dest."""

import subprocess
import sys
from pathlib import Path
from typing import Optional

from duho import Cmd, LoggingArgs

from dotagents.cli._common import _resolve_required_tool


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
        # Front-end onto the single standalone implementation, tools/audit_config.py
        # (D70): resolve the same script the payload installs and some rules
        # reference by path, then shell to it with the requested flags.
        auditor_path = _resolve_required_tool("audit_config.py", root=self.root)
        if auditor_path is None:
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
