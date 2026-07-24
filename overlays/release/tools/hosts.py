"""VCS-host abstraction for the release flow.

`ReleaseHost` is a small base class over the VCS-host surface the release tool
touches: parsing the `origin` remote, monitoring CI/pipeline for a ref, and
pushing. Two concrete impls ship here:

- ``GitHubHost``  — CI via the ``gh`` CLI (``gh run watch`` / ``gh run list``).
- ``GitLabHost``  — pipeline via an external ``gitlabq`` tool (optional; shelled
  to when present, degrades cleanly when not).

All host-agnostic release logic (version bump, commit-plan build/apply,
repository validation, tag creation) stays in ``ReleaseManager`` and does not
belong here. Stdlib + subprocess only; Python 3.9 floor. No network access from
this module itself — every network touch goes through a subprocess to an
external CLI, so tests mock ``subprocess`` and never hit the wire.

``monitor_ci`` returns a uniform dict across hosts::

    {"status": "success" | "failed" | "unknown" | "error",
     "message": "<human-readable>",
     ...host-specific extras (pipeline / runs / logs)... }
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from shutil import which
from typing import Any, Callable, Dict, List, Optional, Tuple


def _parse_remote_url(url: str) -> Optional[Tuple[str, str]]:
    """Parse a git remote URL into ``(host, project_path)``.

    Handles the common forms:
      - scp-like SSH:   ``git@host:group/project(.git)``
      - ssh://:         ``ssh://git@host[:port]/group/project(.git)``
      - https://:       ``https://host[:port]/group/project(.git)``

    ``project_path`` keeps any nested namespace (``group/sub/project``).
    Returns ``None`` when nothing matches.
    """
    url = url.strip()
    # ssh://[user@]host[:port]/path
    m = re.match(r"^ssh://(?:[^@]+@)?([^:/]+)(?::\d+)?/([^\s]+?)(?:\.git)?/?$", url)
    if m:
        return m.group(1), m.group(2)
    # http(s)://host[:port]/path
    m = re.match(r"^https?://(?:[^@/]+@)?([^:/]+)(?::\d+)?/([^\s]+?)(?:\.git)?/?$", url)
    if m:
        return m.group(1), m.group(2)
    # scp-like: [user@]host:path  (no scheme, ':' separates host from path)
    if "://" not in url:
        m = re.match(r"^(?:[^@]+@)?([^:/]+):([^\s]+?)(?:\.git)?/?$", url)
        if m:
            return m.group(1), m.group(2)
    return None


class ReleaseHost:
    """Base class for a VCS host. Subclasses set ``name`` and implement
    :meth:`detect` and :meth:`monitor_ci`."""

    name = "generic"

    def __init__(self, remote_url: str):
        self.remote_url = remote_url
        parsed = _parse_remote_url(remote_url)
        self.host: Optional[str] = parsed[0] if parsed else None
        self.project: Optional[str] = parsed[1] if parsed else None

    # -- factory ---------------------------------------------------------
    @classmethod
    def detect(cls, remote_url: str) -> bool:
        """Does this host own ``remote_url``? Overridden by subclasses."""
        raise NotImplementedError

    @classmethod
    def from_remote(cls, remote_url: Optional[str]) -> Optional["ReleaseHost"]:
        """Pick the concrete host for ``remote_url``, or ``None`` if unknown."""
        if not remote_url:
            return None
        for impl in (GitHubHost, GitLabHost):
            if impl.detect(remote_url):
                return impl(remote_url)
        return None

    # -- shared behavior -------------------------------------------------
    def parse_remote(self, url: Optional[str] = None) -> Optional[Tuple[str, str]]:
        """Return ``(host, project_path)`` for ``url`` (defaults to this
        host's own remote)."""
        return _parse_remote_url(url if url is not None else self.remote_url)

    def monitor_ci(self, ref: str, *, timeout: int = 600) -> Dict[str, Any]:
        """Monitor CI for ``ref``; return the uniform status dict."""
        raise NotImplementedError

    def push(self, run_git: Callable[..., Any], tag: str) -> None:
        """Push the current branch and ``tag``. Host-agnostic default."""
        run_git("push")
        run_git("push", "origin", tag)


class GitHubHost(ReleaseHost):
    """GitHub (github.com and GitHub Enterprise). CI via the ``gh`` CLI, which
    handles its own auth — no token juggling here."""

    name = "github"

    @classmethod
    def detect(cls, remote_url: str) -> bool:
        parsed = _parse_remote_url(remote_url)
        if not parsed:
            return False
        host = parsed[0].lower()
        # github.com and common GitHub Enterprise host shapes.
        if host == "github.com" or host.endswith(".github.com"):
            return True
        if "github" in host:
            return True
        return False

    def _gh(self) -> Optional[str]:
        return which("gh")

    def monitor_ci(self, ref: str, *, timeout: int = 600) -> Dict[str, Any]:
        gh = self._gh()
        if not gh:
            return {
                "status": "unknown",
                "message": "gh CLI not found — install GitHub CLI (gh) to monitor CI",
            }
        # Find the most recent run for this ref (branch or tag).
        list_cmd = [
            gh, "run", "list",
            "--branch", ref,
            "--limit", "1",
            "--json", "databaseId,status,conclusion,url,displayTitle",
        ]
        proc = self._run(list_cmd)
        if proc.returncode != 0:
            return {
                "status": "error",
                "message": "gh run list failed (exit %d)" % proc.returncode,
                "stderr": proc.stderr.strip(),
            }
        try:
            runs: List[Dict[str, Any]] = json.loads(proc.stdout or "[]")
        except json.JSONDecodeError:
            return {
                "status": "error",
                "message": "gh run list returned non-JSON output",
                "stdout": proc.stdout.strip(),
            }
        if not runs:
            return {
                "status": "unknown",
                "message": "No CI runs found for ref %r" % ref,
            }
        run = runs[0]
        run_id = run.get("databaseId")
        url = run.get("url", "no url")
        # If still in progress, watch it to completion (gh handles polling).
        if run.get("status") != "completed" and run_id is not None:
            watch_cmd = [
                gh, "run", "watch", str(run_id),
                "--exit-status",
                "--interval", "10",
            ]
            watch = self._run(watch_cmd, timeout=timeout)
            # Re-fetch the terminal conclusion for a definitive verdict.
            view = self._run([
                gh, "run", "view", str(run_id),
                "--json", "status,conclusion,url",
            ])
            if view.returncode == 0:
                try:
                    fresh = json.loads(view.stdout or "{}")
                    run.update({
                        "status": fresh.get("status", run.get("status")),
                        "conclusion": fresh.get("conclusion", run.get("conclusion")),
                        "url": fresh.get("url", url),
                    })
                    url = run.get("url", url)
                except json.JSONDecodeError:
                    pass
            else:
                # watch itself is our signal if the re-view failed.
                if watch.returncode != 0 and run.get("conclusion") in (None, ""):
                    run["conclusion"] = "failure"
        conclusion = (run.get("conclusion") or "").lower()
        if conclusion == "success":
            return {
                "status": "success",
                "run": run,
                "message": "CI succeeded (%s)" % url,
            }
        if conclusion in ("failure", "cancelled", "timed_out", "action_required"):
            return {
                "status": "failed",
                "run": run,
                "message": "CI %s (%s)" % (conclusion, url),
            }
        return {
            "status": run.get("status") or "unknown",
            "run": run,
            "message": "CI status: %s (%s)" % (run.get("status") or "unknown", url),
        }

    @staticmethod
    def _run(cmd: List[str], timeout: Optional[int] = None) -> subprocess.CompletedProcess:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


class GitLabHost(ReleaseHost):
    """GitLab (gitlab.com and self-hosted). Pipeline monitoring shells to an
    external ``gitlabq`` tool resolved via ``$GITLABQ`` or ``PATH``. When that
    tool is not installed, monitoring degrades to a clean 'unavailable' dict
    (no crash, no reference to any private infrastructure)."""

    name = "gitlab"

    @classmethod
    def detect(cls, remote_url: str) -> bool:
        parsed = _parse_remote_url(remote_url)
        if not parsed:
            return False
        host = parsed[0].lower()
        if host == "gitlab.com" or host.endswith(".gitlab.com"):
            return True
        if "gitlab" in host:
            return True
        return False

    def _gitlabq(self) -> Optional[str]:
        env_gitlabq = os.environ.get("GITLABQ")
        if env_gitlabq:
            candidate = Path(env_gitlabq).expanduser()
            if candidate.exists():
                return str(candidate)
        return which("gitlabq") or which("gitlabq.py")

    def monitor_ci(self, ref: str, *, timeout: int = 600) -> Dict[str, Any]:
        if not self.host or not self.project:
            return {
                "status": "unknown",
                "message": "Could not determine GitLab instance/project from remote",
                "origin": self.remote_url,
            }
        gitlabq = self._gitlabq()
        if not gitlabq:
            return {
                "status": "unknown",
                "message": (
                    "GitLab CI monitoring unavailable — install the gitlabq tool "
                    "(set $GITLABQ or put gitlabq on PATH) to monitor pipelines"
                ),
            }
        cmd = [
            sys.executable,
            gitlabq,
            "pipeline-monitor",
            self.project,
            "--instance", self.host,
            "--ref", ref,
            "--timeout", str(timeout),
            "--include-failures",
            "--include-traces",
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            return {
                "status": "error",
                "message": "gitlabq pipeline-monitor failed (exit %d)" % proc.returncode,
                "stderr": proc.stderr.strip(),
                "stdout": proc.stdout.strip(),
            }
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError:
            return {
                "status": "error",
                "message": "gitlabq pipeline-monitor returned non-JSON output",
                "stdout": proc.stdout.strip(),
                "stderr": proc.stderr.strip(),
            }
        status = data.get("status")
        pipeline = data.get("pipeline") or {}
        if status == "success":
            return {
                "status": "success",
                "pipeline": pipeline,
                "message": "Pipeline succeeded (%s)" % pipeline.get("web_url", "no url"),
            }
        if status == "failed":
            return {
                "status": "failed",
                "pipeline": pipeline,
                "failed_jobs": data.get("failed_jobs", []),
                "logs": data.get("failed_job_traces", {}),
                "message": "Pipeline failed (%s)" % pipeline.get("web_url", "no url"),
            }
        return {
            "status": status or "unknown",
            "pipeline": pipeline,
            "message": data.get("message") or "Pipeline status: %s" % status,
        }
