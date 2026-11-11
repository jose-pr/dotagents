"""Run private-sync's link or sync from this overlay's own library, by path.

    python -I run_link.py link PROJECT AGENTS_DIR
    python -I run_link.py sync PROJECT AGENTS_DIR MESSAGE

The hooks use this instead of the `dotagents` command line: that loads every command and library
the project's own `.agents` holds, and a hook is the program that must not run what the agent
wrote. The library is `../lib/python/_link.py` beside this file; nothing else is imported from
outside the standard library.
"""
import importlib.util
import logging
import sys
from pathlib import Path


def load():
    source = Path(__file__).resolve().parent.parent / "lib" / "python" / "_link.py"
    spec = importlib.util.spec_from_file_location("dotagents_private_sync_link", str(source))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def main(argv):
    if len(argv) < 4 or argv[1] not in ("link", "sync"):
        sys.stderr.write("usage: run_link.py link|sync PROJECT AGENTS_DIR [MESSAGE]\n")
        return 2
    mode, project, agents = argv[1], argv[2], argv[3]
    logging.basicConfig(stream=sys.stderr, level=logging.INFO, format="%(message)s")
    log = logging.getLogger("private-sync")
    link = load()
    if mode == "link":
        link.link_project(project, agents, logger=log)
        return 0
    message = argv[4] if len(argv) > 4 else "dotagents: sync"
    return link.sync_agents(agents, message=message, project_dir=project, logger=log)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
