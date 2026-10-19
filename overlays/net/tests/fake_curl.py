"""A fake real curl for the launcher's tests: an executable named ``curl``
(``curl.exe`` on Windows, made with pip's vendored distlib launcher) that
records what it was started with and exits with ``FAKE_CURL_RC``.

The record (JSON, at ``FAKE_CURL_LOG``, one object per run appended as a
line): ``argv`` (its arguments), ``config`` (the text of each ``-K`` /
``--config`` file it was given, read while the launcher still has it) and
``env`` (the variables named in ``FAKE_CURL_ENV``, ``:``-separated).
"""

import json
import os
import stat
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main():
    argv = sys.argv[1:]
    config = []
    for index, arg in enumerate(argv):
        name = None
        if arg in ("-K", "--config") and index + 1 < len(argv):
            name = argv[index + 1]
        elif arg.startswith("--config="):
            name = arg.split("=", 1)[1]
        if name:
            try:
                config.append(Path(name).read_text(encoding="utf-8"))
            except OSError as exc:
                config.append("<unreadable: %s>" % exc)
    names = [n for n in os.environ.get("FAKE_CURL_ENV", "").split(":") if n]
    record = {"argv": argv, "config": config, "env": {n: os.environ.get(n) for n in names}}
    with open(os.environ["FAKE_CURL_LOG"], "a", encoding="utf-8") as log:
        log.write(json.dumps(record) + "\n")
    return int(os.environ.get("FAKE_CURL_RC", "0"))


def install(directory):
    """Put the fake ``curl`` in ``directory``; return its path. The child
    imports this module, so ``PYTHONPATH`` must name this directory: see
    :func:`environment`."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        from pip._vendor.distlib.scripts import ScriptMaker

        maker = ScriptMaker(None, str(directory))
        maker.executable = sys.executable
        maker.variants = {""}
        made = maker.make("curl = fake_curl:main")
        return Path(next(p for p in made if p.lower().endswith(".exe")))
    path = directory / "curl"
    path.write_text(
        "#!%s\nimport sys\nsys.path.insert(0, %r)\nimport fake_curl\nsys.exit(fake_curl.main())\n"
        % (sys.executable, str(HERE)),
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def environment(log, extra_path=()):
    """The variables a test sets for the fake to run and record into ``log``."""
    return {
        "FAKE_CURL_LOG": str(log),
        "PYTHONPATH": os.pathsep.join([str(HERE), *map(str, extra_path)]),
    }


def records(log):
    path = Path(log)
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
