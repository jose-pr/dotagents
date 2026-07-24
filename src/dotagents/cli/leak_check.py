"""`dotagents leak-check` -- scan a repo for private agent-config leakage.

A thin front-end onto the single standalone implementation
`tools/leak_check.py` (D70): leakage hygiene is a dotagents concern (it guards
private plan names, `.agents/` refs, and agent-session trailers out of a public
repo), so it earns a core subcommand -- but the logic stays in the one script
the payload installs and rules may reference by path. This class only resolves
that script and shells to it with the target repo path.
"""

import subprocess
import sys
from pathlib import Path

from duho import Cmd, LoggingArgs

from dotagents.cli._common import _resolve_required_tool


class LeakCheck(LoggingArgs, Cmd):
    """Scan a repo's tracked files and commit messages for private leakage."""

    _parsername_ = "leak-check"

    repo: Path = Path(".")
    "Repository root to scan (default: current directory)."
    ("repo",)

    commits_only: bool = False
    "Scan only commit messages (skip the tracked-file scan) -- usable as a gate on repos that track .agents/ content."
    ("--commits-only",)

    def __call__(self) -> int:
        checker_path = _resolve_required_tool("leak_check.py")
        if checker_path is None:
            raise SystemExit("error: could not find leak_check.py")
        cmd = [sys.executable, str(checker_path)]
        if self.commits_only:
            cmd.append("--commits-only")
        cmd.append(str(self.repo))
        return subprocess.call(cmd)
