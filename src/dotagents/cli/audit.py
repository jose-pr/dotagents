"""`dotagents audit` -- run the STRUCTURAL config auditor against a payload/dest.

audit validates dotagents-config structure only (manifest, forbidden BASE_PATTERNS,
size budgets, overlay-manifest rules, templates). Personal-leak / hygiene scanning
is NOT audit's job (D84) -- that is the separate `leak-check` tool, run locally.
"""

import subprocess
import sys
from pathlib import Path

from duho import Cmd, LoggingArgs

from dotagents.cli._common import _resolve_required_tool


class Audit(LoggingArgs, Cmd):
    """Run the structural config auditor against a payload/install destination."""

    _parsername_ = "audit"

    root: Path = Path.home() / ".agents"
    "Root directory to audit (an installed dest, or a repo payload/ dir)."
    ("--root",)

    check_templates: bool = False
    "Also run --check-templates (needs Python 3.11+)."
    ("--check-templates",)

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
        return rc
