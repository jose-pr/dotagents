#!/usr/bin/env python3
"""Condense a noisy command's output into a verdict an agent can read cheaply.

Run a build/test/lint command through this instead of reading its raw output.
It prints a short verdict plus only the lines that matter, and writes the full
log to a file you can grep if the verdict isn't enough.

    py -3.12 $ENGINEERING_OVERLAY_ROOT/tools/summarize_run.py -- pytest -q
    py -3.12 $ENGINEERING_OVERLAY_ROOT/tools/summarize_run.py --log build.log -- npm run build

Exits with the wrapped command's exit code, so it drops into CI unchanged (127
when the command cannot be found or started).

The command is found the way a shell finds it: a relative path (``.venv/...``)
from the current directory, a bare name through PATH -- on Windows with PATHEXT,
so a ``.cmd`` / ``.bat`` wrapper (``dotagents`` is ``dotagents.cmd``) runs too.
Without ``--log`` the full output goes to a file of its own in the temp
directory, named for the command and the run, so parallel runs never share one.

Why: a full pytest/webpack/cargo log can be thousands of tokens, and the agent
reads all of them to learn one bit ("did it pass?"). This pays those tokens once,
in a subprocess, instead of once per turn in the context window.
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# Lines worth surfacing even on success — the things you'd actually act on.
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

# A line matching this is noise even if it matched SIGNAL above.
NOISE = re.compile(r"(Downloading|Using cached|Collecting |Requirement already|node_modules)")

MAX_LINES = 40


def resolve_command(cmd):
    """``cmd`` with its program resolved as a shell would: a path with a
    directory part made absolute against the cwd (CreateProcess does not
    resolve a relative ``a/b.exe``), a bare name looked up on PATH with
    ``shutil.which`` (which honours PATHEXT, so ``name`` finds ``name.cmd``).
    Unresolvable: returned unchanged, so the error names what was asked."""
    program = cmd[0]
    if os.path.dirname(program):
        return [os.path.abspath(program), *cmd[1:]]
    found = shutil.which(program)
    return [found, *cmd[1:]] if found else list(cmd)


def default_log(cmd):
    """A log path of this run's own: ``<tmp>/summarize_run/<program>-<time>-<pid>.log``."""
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", os.path.basename(cmd[0]))[:40] or "run"
    stamp = time.strftime("%Y%m%d-%H%M%S")
    directory = Path(tempfile.gettempdir()) / "summarize_run"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / ("%s-%s-%d.log" % (slug, stamp, os.getpid()))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--log", default=None,
                    help="where to write the full output (default: a file of its own in the temp dir)")
    ap.add_argument("--max-lines", type=int, default=MAX_LINES)
    ap.add_argument("command", nargs=argparse.REMAINDER,
                    help="the command, after a literal --")
    args = ap.parse_args()

    cmd = args.command
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]
    if not cmd:
        ap.error("no command given (put it after --)")

    try:
        proc = subprocess.run(resolve_command(cmd), capture_output=True,
                              text=True, encoding="utf-8", errors="replace")
    except OSError as exc:
        print(f"=== FAIL (exit 127): {' '.join(cmd)}")
        print(f"=== could not start {cmd[0]!r}: {exc}")
        return 127
    output = (proc.stdout or "") + (proc.stderr or "")

    log = Path(args.log) if args.log else default_log(cmd)
    log.write_text(output, encoding="utf-8")

    lines = output.splitlines()
    hits = [ln for ln in lines
            if SIGNAL.search(ln) and not NOISE.search(ln)]

    verdict = "PASS" if proc.returncode == 0 else f"FAIL (exit {proc.returncode})"
    print(f"=== {verdict}: {' '.join(cmd)}")
    print(f"=== {len(lines)} lines of output -> {log} ({len(hits)} notable)")

    if hits:
        shown = hits[: args.max_lines]
        print()
        for ln in shown:
            print(ln.rstrip())
        if len(hits) > len(shown):
            print(f"... {len(hits) - len(shown)} more — grep {log}")
    elif proc.returncode != 0:
        # Failed but nothing matched: show the tail, which is usually the reason.
        print()
        for ln in lines[-args.max_lines:]:
            print(ln.rstrip())

    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
