#!/usr/bin/env python3
"""Run `dotagents hook` for a harness's own hook: the harness's payload goes in, its answer comes out.

    overlay_hook.py HARNESS EVENT TOKEN

`init` and `overlays add/sync/remove` write this beside the user's harness configuration and name
it, with the absolute interpreter that ran them, in the harness's hook entries. What it needs is
in `overlay_hook.<TOKEN>.json` next to it, written by the same step: the interpreter, the place
`dotagents` was imported from, the user store, the deadline, and the events that have a guard.
It never looks in the working directory or the project: what is there is the agent's to write.

It runs `dotagents hook` as a child (the interpreter recorded, isolated from the working directory
and the environment's `PYTHON*`), kills the child's whole process tree at the deadline, and
**relays only what it recognizes**: a refusal (`deny`, `ask`, `block`, `continue: false`), context
and a message, in the harness's own shape. An `allow`, a rewritten input, `continue: true` or
anything else is not relayed: for an event with a guard that is a failure, and the answer is a
refusal. Without a guard a failure says nothing. The exit status is always 0: a hook blocks by
what it says.
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

NATIVE = {"session-start": "SessionStart", "session-end": "SessionEnd", "prompt": "UserPromptSubmit",
          "turn-end": "Stop", "cd": "CwdChanged", "pre-compact": "PreCompact", "pre-tool": "PreToolUse",
          "post-tool": "PostToolUse"}
MAX_STDIN = 8 * 1024 * 1024
MAX_ANSWER = 512 * 1024
#: Import dotagents from the recorded place only; argv[1] is that place.
BOOT = ("import sys, runpy; sys.path.insert(0, sys.argv[1]); del sys.argv[1]; "
        "runpy.run_module('dotagents', run_name='__main__', alter_sys=True)")
_BAD = {chr(c) for c in list(range(0, 9)) + [11, 12] + list(range(14, 32)) + list(range(127, 160))}
_BAD |= set("‪‫‬‭‮⁦⁧⁨⁩  ")


def printable(value, bound):
    """A string without control or bidirectional characters, or None when it is not one."""
    if not isinstance(value, str) or len(value) > bound or any(c in _BAD for c in value):
        return None
    return value


def refusal(harness, event, why):
    if event == "pre-tool":
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                       "permissionDecisionReason": why}}
    if event == "pre-compact" and harness == "codex":
        return {"continue": False, "stopReason": why}
    if event in ("prompt", "pre-compact"):
        return {"decision": "block", "reason": why}
    return None


def sanitize(harness, event, raw):
    """What to relay for the child's output, as text; None if it is not an answer this recognizes.
    Only a refusal, context and a message pass, in the shapes the harness knows."""
    if len(raw) > MAX_ANSWER:
        return None
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if not text.strip():
        return ""
    if event == "pre-compact" and harness == "claude-code" and not text.lstrip().startswith("{"):
        # Claude Code takes a compaction's context as plain text.
        return text if all(c in "\n\t" or c not in _BAD for c in text) else None
    try:
        value = json.loads(text)
    except ValueError:
        return None
    if not isinstance(value, dict):
        return None
    out = {}
    for key, item in value.items():
        if key == "systemMessage":
            item = printable(item, 8192)
            if item is None:
                return None
            out[key] = item
        elif key == "hookSpecificOutput" and isinstance(item, dict):
            inner = {}
            for name, thing in item.items():
                if name == "hookEventName" and thing == NATIVE.get(event):
                    inner[name] = thing
                elif name == "additionalContext":
                    thing = printable(thing, 256 * 1024)
                    if thing is None:
                        return None
                    inner[name] = thing
                elif name == "permissionDecision" and (thing == "deny" or (thing == "ask" and harness == "claude-code")):
                    inner[name] = thing
                elif name == "permissionDecisionReason":
                    thing = printable(thing, 2048)
                    if thing is None:
                        return None
                    inner[name] = thing
                else:
                    return None
            out[key] = inner
        elif key == "decision" and item == "block":
            out[key] = item
        elif key == "reason":
            item = printable(item, 2048)
            if item is None:
                return None
            out[key] = item
        elif key == "continue" and item is False:
            out[key] = item
        elif key == "stopReason":
            item = printable(item, 2048)
            if item is None:
                return None
            out[key] = item
        else:
            return None
    return json.dumps(out) + "\n" if out else ""


def read_record(token):
    try:
        with open(str(Path(__file__).resolve().parent / ("overlay_hook.%s.json" % token)), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


class _Job(object):
    """A Windows job object that ends every process in it when closed."""

    def __init__(self):
        import ctypes
        from ctypes import wintypes

        self.k = ctypes.WinDLL("kernel32", use_last_error=True)
        self.k.CreateJobObjectW.restype = wintypes.HANDLE
        self.handle = self.k.CreateJobObjectW(None, None)
        if not self.handle:
            raise OSError("CreateJobObject failed")

        class Basic(ctypes.Structure):
            _fields_ = [("a", ctypes.c_int64), ("b", ctypes.c_int64), ("LimitFlags", wintypes.DWORD),
                        ("c", ctypes.c_size_t), ("d", ctypes.c_size_t), ("e", wintypes.DWORD),
                        ("f", ctypes.c_size_t), ("g", wintypes.DWORD), ("h", wintypes.DWORD)]

        class Io(ctypes.Structure):
            _fields_ = [(n, ctypes.c_uint64) for n in ("r", "w", "o", "rb", "wb", "ob")]

        class Extended(ctypes.Structure):
            _fields_ = [("Basic", Basic), ("Io", Io), ("p", ctypes.c_size_t), ("j", ctypes.c_size_t),
                        ("pk", ctypes.c_size_t), ("jk", ctypes.c_size_t)]

        info = Extended()
        info.Basic.LimitFlags = 0x2000
        if not self.k.SetInformationJobObject(self.handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
            raise OSError("SetInformationJobObject failed")

    def add(self, process):
        import ctypes

        handle = getattr(process, "_handle", None)
        ok = handle is not None and bool(self.k.AssignProcessToJobObject(self.handle, int(handle)))
        if handle is not None:
            ctypes.WinDLL("ntdll").NtResumeProcess(int(handle))
        return ok

    def close(self):
        self.k.TerminateJobObject(self.handle, 1)
        self.k.CloseHandle(self.handle)


def run_tree(argv, payload, timeout, env):
    """``(status, stdout)`` of a child, or None when it ran past ``timeout``; its whole process tree
    (a process group, a job object on Windows) ends with it."""
    options = {"stdin": subprocess.PIPE, "stdout": subprocess.PIPE, "stderr": subprocess.DEVNULL, "env": env}
    job = None
    if os.name == "nt":
        try:
            job = _Job()
        except OSError:
            job = None
        options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | (0x4 if job else 0)
    else:
        options["start_new_session"] = True
    process = subprocess.Popen(argv, **options)
    if job is not None and not job.add(process):
        job.close()
        job = None
    try:
        out, _ = process.communicate(payload, timeout=timeout)
        return process.returncode, out
    except subprocess.TimeoutExpired:
        return None
    finally:
        if job is not None:
            job.close()
        elif os.name != "nt":
            import signal

            try:
                os.killpg(process.pid, signal.SIGKILL)
            except OSError:
                pass
        try:
            process.kill()
        except OSError:
            pass


def main(argv):
    started = time.monotonic()
    if len(argv) < 4:
        return 0
    harness, event, token = argv[1:4]
    record = read_record(token)
    if record is None:
        return 0
    guards = record.get("guards")
    guarded = isinstance(guards, list) and event in guards
    answer = None
    why = "an overlay hook guard could not run"
    try:
        payload = sys.stdin.buffer.read(MAX_STDIN + 1)
        if len(payload) > MAX_STDIN:
            raise ValueError("payload")
        python, root, store = record["python"], record["code_root"], record["store"]
        if os.path.normcase(os.path.realpath(sys.executable)) != os.path.normcase(os.path.realpath(python)):
            raise ValueError("interpreter")
        deadline = float(record.get("deadline", 150))
        level = "user" if token == "user" else "project"
        command = [python, "-I", "-c", BOOT, root, "hook", event, "--harness", harness, "--level", level,
                   "--agents-dir", store]
        env = {k: v for k, v in os.environ.items() if not k.upper().startswith("PYTHON")}
        env["AGENTS_HOME"] = store
        done = run_tree(command, payload, max(1.0, deadline - (time.monotonic() - started)), env)
        if done is not None and done[0] == 0:
            answer = sanitize(harness, event, done[1])
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        answer = None
    if answer is None and guarded:
        refused = refusal(harness, event, why)
        answer = json.dumps(refused) + "\n" if refused else ""
    if answer:
        sys.stdout.write(answer)
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv))
    except Exception:
        sys.exit(0)
