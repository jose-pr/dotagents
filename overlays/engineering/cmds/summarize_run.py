"""`dotagents summarize-run` -- run a noisy command, print a verdict an agent can read cheaply.

    dotagents summarize-run -- pytest -q
    dotagents summarize-run -o build.log -- npm run build

Run a build, test or lint command through this instead of reading its raw
output. It prints one verdict line, where the full log went, and only the
lines worth acting on. A full test or build log can be thousands of tokens
read to learn one bit; this pays them once, in a subprocess.

The exit code is the wrapped command's (127 when it cannot be found or
started), so it drops into CI unchanged.

The command is found the way a shell finds it: a relative path from the
current directory, a bare name through PATH -- on Windows with PATHEXT, so a
``.cmd`` or ``.bat`` wrapper runs too. Without ``--output`` the full output goes
to a file of its own in the temp directory, named for the command and the
run, so parallel runs never share one.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from duho import Cmd, LoggingArgs

#: Lines worth surfacing even on success: the things you would act on.
SIGNAL = re.compile(
    r"""
    ^\s*(E\s|FAILED|ERROR|error(\[|:)|FAIL\b|panic:|Traceback|
    \s*Assertion|AssertionError|SyntaxError|
    warning:\s|WARNING:|
    \d+\s+(passed|failed|error|skipped)|
    Tests?:|Summary|
    \s*✗|\s*×)
    """,
    re.VERBOSE | re.IGNORECASE,
)
#: A line matching this is noise even if it matched SIGNAL.
NOISE = re.compile(r"(Downloading|Using cached|Collecting |Requirement already|node_modules)")

EXIT_NOT_STARTED = 127


class SummarizeRun(LoggingArgs, Cmd):
    """Run a command; print a verdict, the notable lines, and where the full log is."""

    _parsername_ = "summarize-run"

    command: "list[str]" = []
    "The command and its arguments. On a command line, put it after a literal --."
    ("command",)

    output: str = ""
    "Where to write the full output (default: a file of its own in the temp directory)."
    ("--output", "-o")

    max_lines: int = 40
    "How many notable lines to print."
    ("--max-lines",)

    timeout: float = 0.0
    "Seconds before the command is stopped (default: no limit)."
    ("--timeout",)

    def __call__(self) -> int:
        cmd = list(self.command) or list(getattr(self, "_passthrough_", None) or [])
        if cmd and cmd[0] == "--":
            cmd = cmd[1:]
        if not cmd:
            raise SystemExit("error: no command given (put it after --)")
        shown = " ".join(cmd)

        try:
            proc = subprocess.run(
                self._resolve(cmd), capture_output=True, stdin=subprocess.DEVNULL,
                text=True, encoding="utf-8", errors="replace",
                timeout=self.timeout or None)
        except subprocess.TimeoutExpired as exc:
            output = self._text(exc.stdout) + self._text(exc.stderr)
            log = self._write_log(cmd, output)
            print("=== FAIL (timed out after %gs): %s" % (self.timeout, shown))
            print("=== %d lines of output -> %s" % (len(output.splitlines()), log))
            return EXIT_NOT_STARTED
        except OSError as exc:
            print("=== FAIL (exit %d): %s" % (EXIT_NOT_STARTED, shown))
            print("=== could not start %r: %s" % (cmd[0], exc))
            return EXIT_NOT_STARTED

        output = (proc.stdout or "") + (proc.stderr or "")
        log = self._write_log(cmd, output)
        lines = output.splitlines()
        notable = [ln for ln in lines if SIGNAL.search(ln) and not NOISE.search(ln)]

        verdict = "PASS" if proc.returncode == 0 else "FAIL (exit %d)" % proc.returncode
        print("=== %s: %s" % (verdict, shown))
        print("=== %d lines of output -> %s (%d notable)" % (len(lines), log, len(notable)))
        self._print_lines(lines, notable, failed=proc.returncode != 0, log=log)
        return proc.returncode

    def _print_lines(self, lines: "list[str]", notable: "list[str]", *,
                     failed: bool, log: Path) -> None:
        if notable:
            print()
            for ln in notable[: self.max_lines]:
                print(ln.rstrip())
            if len(notable) > self.max_lines:
                print("... %d more — grep %s" % (len(notable) - self.max_lines, log))
        elif failed:
            # Failed and nothing matched: the tail is usually the reason.
            print()
            for ln in lines[-self.max_lines:]:
                print(ln.rstrip())

    def _write_log(self, cmd: "list[str]", output: str) -> Path:
        log = Path(self.output) if self.output else self._default_log(cmd)
        with open(log, "w", encoding="utf-8", newline="") as handle:
            handle.write(output)
        return log

    @staticmethod
    def _resolve(cmd: "list[str]") -> "list[str]":
        """`cmd` with its program resolved as a shell would. A path with a
        directory part is made absolute, since process creation on Windows
        does not resolve a relative one; a bare name is looked up on PATH,
        honouring PATHEXT. Unresolvable: unchanged, so the error names what
        was asked for."""
        program = cmd[0]
        if os.path.dirname(program):
            return [os.path.abspath(program), *cmd[1:]]
        found = shutil.which(program)
        return [found, *cmd[1:]] if found else list(cmd)

    @staticmethod
    def _default_log(cmd: "list[str]") -> Path:
        """A log path of this run's own, under the temp directory."""
        slug = re.sub(r"[^A-Za-z0-9._-]+", "_", os.path.basename(cmd[0]))[:40] or "run"
        directory = Path(tempfile.gettempdir()) / "summarize_run"
        directory.mkdir(parents=True, exist_ok=True)
        return directory / ("%s-%s-%d-%d.log" % (
            slug, time.strftime("%Y%m%d-%H%M%S"), os.getpid(), time.monotonic_ns() % 10**9))

    @staticmethod
    def _text(stream: "str | bytes | None") -> str:
        if stream is None:
            return ""
        return stream if isinstance(stream, str) else stream.decode("utf-8", "replace")
