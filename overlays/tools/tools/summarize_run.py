#!/usr/bin/env python3
"""Condense a noisy command's output into a verdict an agent can read cheaply.

Run a build/test/lint command through this instead of reading its raw output.
It prints a short verdict plus only the lines that matter, and writes the full
log to a file you can grep if the verdict isn't enough.

    py -3.12 ~/.agents/tools/summarize_run.py -- pytest -q
    py -3.12 ~/.agents/tools/summarize_run.py --log build.log -- npm run build

Exits with the wrapped command's exit code, so it drops into CI unchanged.

Why: a full pytest/webpack/cargo log can be thousands of tokens, and the agent
reads all of them to learn one bit ("did it pass?"). This pays those tokens once,
in a subprocess, instead of once per turn in the context window.
"""
import argparse
import re
import subprocess
import sys
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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--log", default="run.log", help="where to write the full output")
    ap.add_argument("--max-lines", type=int, default=MAX_LINES)
    ap.add_argument("command", nargs=argparse.REMAINDER,
                    help="the command, after a literal --")
    args = ap.parse_args()

    cmd = args.command
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]
    if not cmd:
        ap.error("no command given (put it after --)")

    proc = subprocess.run(cmd, capture_output=True, text=True)
    output = (proc.stdout or "") + (proc.stderr or "")

    log = Path(args.log)
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
