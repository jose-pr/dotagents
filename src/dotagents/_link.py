"""Private-agents linking and git sync.

Model: your global ``~/.agents`` IS a private git repo. Per-project private
agent state (plans, kb, findings, a user-managed ``AGENTS.md``) lives under
``~/.agents/projects/<name>/`` and is symlinked into each checkout as
``<project>/.agents``. One private repo carries the global config AND every
project's private agent state; the public project repos never track any of it
(the Leakage rule already ``.gitignore``s ``.agents/``).

- ``link_project`` — symlink (or ``--copy``) ``<project>/.agents`` to its store,
  adopting an existing real ``.agents/`` into the store on the first link.
- ``sync_agents`` — copy a copy-mode project's ``.agents`` back into the store,
  then ``git pull --rebase`` / commit / push the private repo.

Kept out of ``cli.py`` (which only wires args to these) to match ``_merge.py`` /
``_sync.py`` / ``_overlays.py``.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional


def project_store(agents_dir: Path, name: str) -> Path:
    """Path to a project's private store inside the global agents repo."""
    return agents_dir / "projects" / name


def resolve_name(project_dir: Path, name: Optional[str]) -> str:
    """Store name for a project: an explicit ``--name`` or the dir's basename."""
    return name or Path(project_dir).resolve().name


def _copy_tree(src: Path, dst: Path, *, overwrite: bool) -> int:
    """Recursively copy files from ``src`` into ``dst``. Returns the file count.
    With ``overwrite=False`` an existing destination file is left untouched."""
    count = 0
    for root, _dirs, files in os.walk(src):
        rel = Path(root).relative_to(src)
        target_root = dst / rel
        target_root.mkdir(parents=True, exist_ok=True)
        for fname in files:
            target = target_root / fname
            if target.exists() and not overwrite:
                continue
            shutil.copy2(str(Path(root) / fname), str(target))
            count += 1
    return count


def _store_has_real_content(store: Path) -> bool:
    """True if the store holds anything beyond the seed skeleton (a `.gitkeep`)."""
    if not store.exists():
        return False
    for p in store.rglob("*"):
        if p.is_file() and p.name != ".gitkeep":
            return True
    return False


def _seed_store(store: Path, dry_run: bool) -> None:
    """Create an empty store skeleton (a `plans/` dir kept by a `.gitkeep`)."""
    if dry_run:
        return
    plans = store / "plans"
    plans.mkdir(parents=True, exist_ok=True)
    keep = plans / ".gitkeep"
    if not keep.exists():
        keep.write_text("", encoding="utf-8")


def _is_link_to(target: Path, store: Path) -> bool:
    """True if ``target`` is a symlink resolving to ``store``."""
    if not os.path.islink(str(target)):
        return False
    try:
        return Path(os.path.realpath(str(target))) == store.resolve()
    except OSError:
        return False


def _gitignore_excludes_agents(project_dir: Path) -> bool:
    gi = project_dir / ".gitignore"
    if not gi.exists():
        return False
    for line in gi.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.strip().rstrip("/") in (".agents", "/.agents"):
            return True
    return False


def _next_backup(project_dir: Path) -> Path:
    cand = project_dir / ".agents.bak"
    i = 1
    while cand.exists() or os.path.islink(str(cand)):
        cand = project_dir / (".agents.bak%d" % i)
        i += 1
    return cand


class _Log:
    def __init__(self, logger):
        self._logger = logger

    def __call__(self, msg, *args):
        if self._logger is not None:
            self._logger.info(msg, *args)


def link_project(
    project_dir,
    agents_dir,
    *,
    name: Optional[str] = None,
    copy: bool = False,
    force: bool = False,
    dry_run: bool = False,
    logger=None,
) -> str:
    """Link ``<project>/.agents`` to ``<agents_dir>/projects/<name>``.

    Default is a symlink; ``--copy`` (or a symlink failure, e.g. Windows without
    privilege) mirrors the store into the project as a real directory instead.
    An existing real ``.agents/`` is *adopted* into an empty store on first link
    (its content moves into the private repo, then the link replaces it). A
    conflict (both the project's ``.agents`` and the store carry real content)
    needs ``--force`` (which backs the project copy up and keeps the store)."""
    log = _Log(logger)
    project_dir = Path(project_dir).expanduser().resolve()
    agents_dir = Path(agents_dir).expanduser().resolve()
    if not project_dir.is_dir():
        raise SystemExit("error: project path is not a directory: %s" % project_dir)

    name = resolve_name(project_dir, name)
    store = project_store(agents_dir, name)
    target = project_dir / ".agents"
    target_is_link = os.path.islink(str(target))

    # Already correctly linked -> idempotent.
    if target_is_link and _is_link_to(target, store):
        if not store.exists():
            _seed_store(store, dry_run)
        log("linked (already): %s/.agents -> %s", project_dir.name, store)
        _warn_gitignore(project_dir, log)
        return name

    # A real .agents dir: adopt into an empty store, or flag a conflict.
    if not target_is_link and target.is_dir():
        target_has = any(p.is_file() for p in target.rglob("*"))
        if not _store_has_real_content(store):
            if target_has:
                log("adopt: %s/.agents -> store %s", project_dir.name, store)
                if not dry_run:
                    _seed_store(store, False)
                    _copy_tree(target, store, overwrite=False)
                    shutil.rmtree(str(target))
            else:
                if not dry_run:
                    shutil.rmtree(str(target))
        elif target_has:
            if not force:
                raise SystemExit(
                    "error: both %s/.agents and the store %s carry content; re-run "
                    "with --force to back up the project copy and use the store"
                    % (project_dir.name, store)
                )
            backup = _next_backup(project_dir)
            log("force: backing up %s/.agents -> %s", project_dir.name, backup.name)
            if not dry_run:
                shutil.move(str(target), str(backup))
        else:  # store has content, target is an empty dir
            if not dry_run:
                shutil.rmtree(str(target))
        target_is_link = False
    elif target_is_link:
        # Symlink pointing somewhere else.
        if not force:
            raise SystemExit(
                "error: %s/.agents is already a symlink elsewhere; re-run with --force"
                % project_dir.name
            )
        log("force: replacing existing symlink %s/.agents", project_dir.name)
        if not dry_run:
            os.unlink(str(target))
        target_is_link = False

    # Ensure the store exists (seed an empty skeleton if brand new).
    if not _store_has_real_content(store):
        _seed_store(store, dry_run)
        log("store: %s", store)

    if copy:
        n = 0 if dry_run else _copy_tree(store, target, overwrite=True)
        log("copied store -> %s/.agents (%s file(s)) [copy mode]", project_dir.name,
            "?" if dry_run else n)
    else:
        if not dry_run:
            try:
                os.symlink(str(store), str(target), target_is_directory=True)
            except OSError as exc:
                log("symlink failed (%s); falling back to a copy", exc)
                _copy_tree(store, target, overwrite=True)
                log("copied store -> %s/.agents [copy fallback]", project_dir.name)
                _warn_gitignore(project_dir, log)
                return name
        log("linked: %s/.agents -> %s", project_dir.name, store)

    _warn_gitignore(project_dir, log)
    return name


def _warn_gitignore(project_dir: Path, log) -> None:
    if not _gitignore_excludes_agents(project_dir):
        log(
            "WARN: %s/.gitignore does not exclude .agents/ -- add a '.agents/' line so "
            "the link is never committed to the project repo",
            project_dir.name,
        )


def _git(agents_dir: Path, *args: str, capture: bool = False):
    return subprocess.run(
        ["git", "-C", str(agents_dir), *args],
        capture_output=capture,
        text=True,
        check=False,
    )


def _has_origin(agents_dir: Path) -> bool:
    res = _git(agents_dir, "remote", capture=True)
    return "origin" in (res.stdout or "").split()


def sync_agents(
    agents_dir,
    *,
    message: str = "dotagents: sync",
    project_dir=None,
    name: Optional[str] = None,
    remote: Optional[str] = None,
    pull: bool = True,
    push: bool = True,
    dry_run: bool = False,
    logger=None,
) -> int:
    """Sync the private agents repo: copy a copy-mode project's ``.agents`` back
    into its store, then ``git pull --rebase`` / commit / push.

    Symlinked projects need no copy-back (their ``.agents`` *is* the store). With
    ``--remote`` on a non-git ``agents_dir``, ``git init`` + set ``origin`` first,
    making first-time bootstrap a single command."""
    log = _Log(logger)
    agents_dir = Path(agents_dir).expanduser().resolve()

    # Copy-back for a copy-mode (non-symlink) project.
    if project_dir is not None:
        project_dir = Path(project_dir).expanduser().resolve()
        target = project_dir / ".agents"
        if target.is_dir() and not os.path.islink(str(target)):
            store = project_store(agents_dir, resolve_name(project_dir, name))
            log("copy-back: %s/.agents -> %s", project_dir.name, store)
            if not dry_run:
                store.mkdir(parents=True, exist_ok=True)
                _copy_tree(target, store, overwrite=True)

    is_git = (agents_dir / ".git").exists()
    if not is_git:
        if remote is None:
            log(
                "WARN: %s is not a git repo and no --remote given; skipping git sync. "
                "Bootstrap with `dotagents sync --remote <url>` or `git init` there.",
                agents_dir,
            )
            return 0
        log("git init: %s (origin=%s)", agents_dir, remote)
        if not dry_run:
            _git(agents_dir, "init")
            _git(agents_dir, "symbolic-ref", "HEAD", "refs/heads/main")
            _git(agents_dir, "remote", "add", "origin", remote)
    elif remote is not None and not _has_origin(agents_dir):
        log("git remote add origin %s", remote)
        if not dry_run:
            _git(agents_dir, "remote", "add", "origin", remote)

    if dry_run:
        log("[dry-run] would git add -A && commit -m %r%s", message,
            " && push" if push else "")
        return 0

    if pull and _has_origin(agents_dir):
        log("git pull --rebase --autostash")
        res = _git(agents_dir, "pull", "--rebase", "--autostash", "origin", "HEAD",
                   capture=True)
        if res.returncode != 0:
            log("note: pull skipped/failed (%s)", (res.stderr or "").strip().splitlines()[-1:]
                and (res.stderr or "").strip().splitlines()[-1] or "no upstream yet")

    _git(agents_dir, "add", "-A")
    status = _git(agents_dir, "status", "--porcelain", capture=True)
    if (status.stdout or "").strip():
        log("git commit -m %r", message)
        _git(agents_dir, "commit", "-m", message)
    else:
        log("nothing to commit")

    if push and _has_origin(agents_dir):
        log("git push -u origin HEAD")
        res = _git(agents_dir, "push", "-u", "origin", "HEAD", capture=True)
        if res.returncode != 0:
            log("WARN: git push failed: %s", (res.stderr or "").strip())
    return 0
