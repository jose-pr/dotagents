"""A throwaway store with an installed overlay whose hook is a script that does what the
environment tells it, for the tests of overlay hooks (tests only; not a test module)."""

from __future__ import annotations

import json
import os
from pathlib import Path

from dotagents import _hook_dispatch, _overlay_hooks
from dotagents._scope import Scope

#: One hook script for every test: it logs what it was given, then does what ``HOOK_*`` says.
HOOK_PY = '''\
import json, os, sys, time
raw = sys.stdin.read()
payload = json.loads(raw) if raw else None
log = os.environ.get("HOOK_LOG")
if log:
    with open(log, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"argv": sys.argv[1:], "event": os.environ.get("AOS_HOOK_EVENT"),
                             "payload": payload, "cwd": os.getcwd(),
                             "saw": sorted(k for k in os.environ if k.startswith(("SECRET_", "CLAUDE_", "CODEX_")) or k in ("SEEN", "AGENTS_SESSION_BASE"))}) + "\\n")
if os.environ.get("HOOK_ERR"):
    sys.stderr.write(os.environ["HOOK_ERR"])
if os.environ.get("HOOK_SLEEP"):
    time.sleep(float(os.environ["HOOK_SLEEP"]))
if os.environ.get("HOOK_BIG"):
    sys.stdout.write("x" * int(os.environ["HOOK_BIG"]))
elif os.environ.get("HOOK_OUT"):
    sys.stdout.write(os.environ["HOOK_OUT"])
sys.exit(int(os.environ.get("HOOK_EXIT", "0")))
'''


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(str(path), "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    return path


class Rig(object):
    """A user store with overlay ``name`` installed and the helpers to fire events at it."""

    def __init__(self, tmp_path: Path, manifest: str = "", name: str = "guard", extra: "dict | None" = None,
                 script: str = HOOK_PY):
        self.store = tmp_path / "store"
        self.log = tmp_path / "hook.log"
        self.root = self.store / "overlays" / name
        self.name = name
        write(self.store / "AGENTS.md", "<!-- dotagents:begin -->\n# BASE\n<!-- dotagents:end -->\n")
        write(self.root / "hook.py", script)
        if manifest:
            write(self.root / "overlay.cfg", manifest)
        for rel, text in (extra or {}).items():
            write(self.root / rel, text)
        self.environ = dict(os.environ, HOOK_LOG=str(self.log))

    @property
    def scope(self) -> Scope:
        return Scope("user", self.store)

    @property
    def overlay(self):
        return next(o for o in self.scope.installed_overlays if o.name == self.name)

    def digest(self) -> str:
        return self.overlay.tree_digest()

    def allow(self) -> str:
        digest = self.digest()
        _overlay_hooks.allow(self.store, self.name, digest)
        return digest

    def fire(self, harness: str, event: str, native: "dict | None" = None, **environ):
        """``(stdout, stderr)`` of the dispatcher for a native payload."""
        env = dict(self.environ, **environ)
        return _hook_dispatch.dispatch(harness, event, json.dumps(native or {"session_id": "s1", "cwd": str(self.store)}).encode(),
                                       self.scope, "user", env)

    def calls(self) -> "list[dict]":
        try:
            return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]
        except OSError:
            return []


def store_env(tmp_path: Path, monkeypatch) -> None:
    """The user store a Rig builds is the one a session resolves."""
    monkeypatch.setenv("AGENTS_HOME", str(tmp_path / "store"))
