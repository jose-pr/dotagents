"""Optional per-project ``.agents`` linking, and a sync hand-off.

Neither of these is required to use dotagents: ``init``/``install`` never touch a
project directory. They exist for one workflow -- keeping each checkout's private
agent state (plans, kb, findings, a user-managed ``AGENTS.md``) outside the public
project repo, in a single place that can be carried between machines.

What dotagents actually does here: point ``<project>/.agents`` at a store, and
reconcile the copy it made if symlinking wasn't available. Two things around that
are **conventions, not requirements**:

- **Where stores live.** ``<agents_dir>/projects/<name>`` is only the default; see
  ``store_root`` (``--store-dir`` / ``DOTAGENTS_STORE_DIR``, absolute paths allowed).
- **How a store reaches other machines.** ``hooks/sync`` owns that if present. The
  bundled git path is a convenience, not the model -- a store that never leaves the
  machine is a perfectly valid setup.

- ``link_project`` — symlink (or ``--copy``) ``<project>/.agents`` to its store,
  adopting an existing real ``.agents/`` into the store on the first link.
- ``sync_agents`` — reconcile a copy-mode project, then hand off to ``hooks/sync``
  or the built-in git path.

Kept out of ``cli.py`` (which only wires args to these) to match ``_merge.py`` /
``_sync.py`` / ``_overlays.py``.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional


#: Default subdirectory of the agents dir that holds per-project stores. This is
#: a convention, not a requirement -- dotagents does not care how you file them.
#: Override per-invocation with ``--store-dir`` or ``DOTAGENTS_STORE_DIR`` (which
#: may be an absolute path, putting the stores outside the agents dir entirely).
DEFAULT_STORE_SUBDIR = "projects"


def store_root(agents_dir: Path, store_dir: "str | os.PathLike | None" = None) -> Path:
    """Directory holding the per-project stores.

    Resolution order: explicit argument, ``DOTAGENTS_STORE_DIR``, then
    ``<agents_dir>/projects``. An absolute value is used as-is."""
    raw = store_dir or os.environ.get("DOTAGENTS_STORE_DIR") or DEFAULT_STORE_SUBDIR
    p = Path(raw).expanduser()
    return p if p.is_absolute() else agents_dir / p


def project_store(
    agents_dir: Path, name: str, store_dir: "str | os.PathLike | None" = None
) -> Path:
    """Path to a project's private store."""
    return store_root(agents_dir, store_dir) / name


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
    """True if ``.gitignore`` actually excludes this project's ``.agents``.

    A symlink named ``.agents`` is a file to git, so a directory-only pattern
    (``.agents/``) does NOT ignore it -- only a slashless ``.agents`` / ``/.agents``
    does. A copy-mode real directory is excluded by either form, so we only insist
    on a slashless pattern when the link is actually a symlink."""
    gi = project_dir / ".gitignore"
    if not gi.exists():
        return False
    symlinked = os.path.islink(str(project_dir / ".agents"))
    for raw in gi.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line in (".agents", "/.agents"):
            return True
        if not symlinked and line.rstrip("/") in (".agents", "/.agents"):
            return True
    return False


def _tracked_agents_files(project_dir: Path) -> int:
    """How many files under ``<project>/.agents`` the project's git repo tracks.

    Adoption is destructive to *tracked* content: it copies the directory into
    the store and then removes it, which git records as deleting every one of
    those files. A repo that deliberately tracks its ``.agents/`` (the dotagents
    repo itself does -- its sanitized design log is public) must therefore never
    be linked. The D43 git-checkout guard does not cover this: a tracked plain
    directory has no ``.git`` of its own. Returns 0 when not a git repo."""
    try:
        res = subprocess.run(
            ["git", "-C", str(project_dir), "ls-files", "--", ".agents"],
            capture_output=True, text=True,
        )
    except OSError:
        return 0
    if res.returncode != 0:
        return 0
    return len([ln for ln in res.stdout.splitlines() if ln.strip()])


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
    store_dir: "str | os.PathLike | None" = None,
    copy: bool = False,
    force: bool = False,
    dry_run: bool = False,
    logger=None,
) -> str:
    """Link ``<project>/.agents`` to its store (default ``<agents_dir>/projects/<name>``;
    see ``store_root`` -- the layout is a convention, overridable with ``store_dir``).

    Default is a symlink; ``--copy`` (or a symlink failure, e.g. Windows without
    privilege) mirrors the store into the project as a real directory instead.
    An existing real ``.agents/`` is *adopted* into an empty store on first link
    (its content moves into the private repo, then the link replaces it). A
    conflict (both the project's ``.agents`` and the store carry real content)
    needs ``--force`` (which backs the project copy up and keeps the store).
    A ``.agents`` that is itself a git checkout is never adopted -- it is left
    in place untouched (``--force`` backs it up and links the store instead)."""
    log = _Log(logger)
    project_dir = Path(project_dir).expanduser().resolve()
    agents_dir = Path(agents_dir).expanduser().resolve()
    if not project_dir.is_dir():
        raise SystemExit("error: project path is not a directory: %s" % project_dir)

    name = resolve_name(project_dir, name)
    store = project_store(agents_dir, name, store_dir)
    target = project_dir / ".agents"
    target_is_link = os.path.islink(str(target))

    # Already correctly linked -> idempotent.
    if target_is_link and _is_link_to(target, store):
        if not store.exists():
            _seed_store(store, dry_run)
        log("linked (already): %s/.agents -> %s", project_dir.name, store)
        _warn_gitignore(project_dir, log)
        return name

    # A .agents whose files are TRACKED by the project's own repo is public
    # content, not a private store: adopting it would rmtree the directory and
    # git would record every tracked file as deleted (D55). Refuse outright --
    # unlike the conflict cases below there is no --force story, because the
    # correct fix is "don't link this repo", never "back it up and link anyway".
    if not target_is_link and target.is_dir():
        n_tracked = _tracked_agents_files(project_dir)
        if n_tracked:
            raise SystemExit(
                "error: %s/.agents has %d file(s) tracked by this repo; linking "
                "would delete them from it. This repo keeps a real .agents/ "
                "(see D55) -- do not link it."
                % (project_dir.name, n_tracked)
            )

    # A .agents that is itself a git checkout (`.git` is a dir, or a file for
    # worktrees) is managed by something else -- e.g. a hosted-runner session
    # that lists the agents repo as a source and clones it to <project>/.agents.
    # Adopting it would move the whole checkout (.git, foreign remote, branch
    # state) into the store, nesting a repo inside the private repo; a later
    # sync's `git add` would then record it as a bare gitlink. Leave it alone.
    if not target_is_link and target.is_dir() and (target / ".git").exists():
        if not force:
            log(
                "skip: %s/.agents is itself a git checkout; leaving it in place "
                "(re-run with --force to back it up and link the store)",
                project_dir.name,
            )
            _warn_gitignore(project_dir, log)
            return name
        backup = _next_backup(project_dir)
        log("force: backing up git checkout %s/.agents -> %s",
            project_dir.name, backup.name)
        if not dry_run:
            shutil.move(str(target), str(backup))
        target_is_link = False

    # A real .agents dir: adopt into an empty store, or flag a conflict.
    elif not target_is_link and target.is_dir():
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
            "WARN: %s/.gitignore does not exclude .agents -- add a '.agents' line "
            "(no trailing slash, so it also covers the symlink) so the link is never "
            "committed to the project repo",
            project_dir.name,
        )


def _git(
    agents_dir: Path,
    *args: str,
    capture: bool = False,
    config_args: "Optional[list[str]]" = None,
    env: "Optional[dict]" = None,
):
    run_env = {**os.environ, **env} if env else None
    return subprocess.run(
        ["git", *(config_args or []), "-C", str(agents_dir), *args],
        capture_output=capture,
        text=True,
        check=False,
        env=run_env,
    )


def _has_origin(agents_dir: Path) -> bool:
    res = _git(agents_dir, "remote", capture=True)
    return "origin" in (res.stdout or "").split()


# Credential helper snippet: reads the PAT from the environment at auth time, so
# the token is never written to .git/config or any file on disk. Mirrors
# overlays/private-sync/hooks/_agents-git-auth.sh.
_GITHUB_CRED_HELPER = (
    '!f() { printf "username=x-access-token\\npassword=%s\\n" '
    '"$DOTAGENTS_AGENTS_TOKEN"; }; f'
)


def _github_proxy_rewrite_active() -> bool:
    """True when a global ``url.<other>.insteadOf https://github.com/...`` rewrite
    is in effect -- a hosted runner routing github traffic to a scoped in-session
    proxy that will not serve an out-of-scope private repo (403). SSH->HTTPS
    convenience rewrites have ``git@``/``ssh://`` values, so they don't match."""
    res = subprocess.run(
        ["git", "config", "--get-regexp", r"^url\..*\.insteadof$"],
        capture_output=True, text=True, check=False,
    )
    for line in (res.stdout or "").splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2 and parts[1].strip().lower().startswith("https://github.com"):
            return True
    return False


def _agents_git_auth(log) -> "tuple[list[str], Optional[dict], Optional[str]]":
    """Authenticate the private agents repo directly against github.com when
    ``DOTAGENTS_AGENTS_TOKEN`` is set, so a standalone ``dotagents sync`` reaches
    the repo the same way the private-sync Stop hook does (it sources
    ``_agents-git-auth.sh``; a direct CLI run never did, so it 403'd through the
    scoped proxy). Returns ``(config_args, env_overlay, tmp_cfg_path)``:

    * no token -> ``([], None, None)`` (no-op; ordinary git).
    * token, no github->proxy rewrite (e.g. a local machine) -> a per-command
      credential helper via ``-c`` (no global-config mutation).
    * token + rewrite active (hosted runner) -> an isolated git config that
      BYPASSES the rewrite so the token authenticates directly against github.com,
      returned as a ``GIT_CONFIG_GLOBAL`` env overlay plus the temp file to unlink.
    """
    if not os.environ.get("DOTAGENTS_AGENTS_TOKEN"):
        return [], None, None

    if not _github_proxy_rewrite_active():
        return (
            ["-c", "credential.https://github.com.helper=" + _GITHUB_CRED_HELPER,
             "-c", "credential.https://github.com.useHttpPath=false"],
            None,
            None,
        )

    def _cur(key: str) -> str:
        r = subprocess.run(["git", "config", key], capture_output=True, text=True,
                           check=False)
        return (r.stdout or "").strip()

    # Preserve the effective identity so a rebase/commit under the isolated config
    # still has a committer.
    name = _cur("user.name") or "dotagents"
    email = _cur("user.email") or "dotagents@localhost"

    fd, cfg = tempfile.mkstemp(prefix="dotagents-git-", suffix=".cfg")
    os.close(fd)

    def _set(key: str, val: str) -> None:
        subprocess.run(["git", "config", "--file", cfg, key, val], check=False)

    _set("credential.https://github.com.helper", _GITHUB_CRED_HELPER)
    _set("credential.https://github.com.useHttpPath", "false")
    _set("init.defaultBranch", "main")
    _set("user.name", name)
    _set("user.email", email)
    # Re-terminated-TLS proxies need the CA bundle; fall back to the system trust
    # store when none of these point at a readable file.
    for ca in (os.environ.get("GIT_SSL_CAINFO"), os.environ.get("SSL_CERT_FILE"),
               os.environ.get("CURL_CA_BUNDLE"), os.environ.get("REQUESTS_CA_BUNDLE"),
               "/root/.ccr/ca-bundle.crt",
               os.path.join(os.environ.get("HOME", ""), ".ccr/ca-bundle.crt")):
        if ca and os.path.isfile(ca):
            _set("http.sslCAInfo", ca)
            break

    log("github.com git is rewritten to an in-session proxy; bypassing it for token auth")
    return [], {"GIT_CONFIG_GLOBAL": cfg, "GIT_CONFIG_SYSTEM": "/dev/null"}, cfg


#: Hook script run by ``sync`` instead of the built-in git path, if present.
#: Tried in order; the first executable one wins.
SYNC_HOOK_NAMES = ("sync", "sync.sh", "sync.cmd", "sync.bat")


def _find_sync_hook(agents_dir: Path) -> "Path | None":
    hooks = agents_dir / "hooks"
    if not hooks.is_dir():
        return None
    for name in SYNC_HOOK_NAMES:
        cand = hooks / name
        if cand.is_file():
            return cand
    return None


def _run_sync_hook(agents_dir: Path, *, message: str, dry_run: bool, log) -> "int | None":
    """Run ``<agents_dir>/hooks/sync`` if it exists; return its exit code, else None.

    Transport is not dotagents' concern -- git is only the bundled default. A hook
    lets the store reach other machines however the user wants (or not at all).
    It receives the store path as ``$1`` and the message as ``$2``, plus
    ``DOTAGENTS_AGENTS_DIR`` / ``DOTAGENTS_SYNC_MESSAGE`` in the environment.
    A non-zero exit is reported and returned; it does NOT fall through to git,
    since a failed hook means the user's own sync failed."""
    hook = _find_sync_hook(agents_dir)
    if hook is None:
        return None

    log("sync hook: %s", hook)
    if dry_run:
        log("[dry-run] would run %s %s %r", hook.name, agents_dir, message)
        return 0

    env = dict(os.environ)
    env["DOTAGENTS_AGENTS_DIR"] = str(agents_dir)
    env["DOTAGENTS_SYNC_MESSAGE"] = message
    cmd = [str(hook), str(agents_dir), message]
    if hook.suffix.lower() in (".sh", "") and os.name == "nt":
        # .sh (and extensionless) are not directly executable on Windows.
        cmd = ["sh"] + cmd
    try:
        res = subprocess.run(cmd, cwd=str(agents_dir), env=env)
    except OSError as exc:
        log("sync hook failed to start (%s); falling back to the built-in git path", exc)
        return None
    if res.returncode != 0:
        log("sync hook exited %d", res.returncode)
    return res.returncode


def sync_agents(
    agents_dir,
    *,
    message: str = "dotagents: sync",
    project_dir=None,
    name: Optional[str] = None,
    store_dir: "str | os.PathLike | None" = None,
    remote: Optional[str] = None,
    pull: bool = True,
    push: bool = True,
    dry_run: bool = False,
    logger=None,
) -> int:
    """Sync the agents store: copy a copy-mode project's ``.agents`` back into it,
    then hand off to whatever moves it between machines.

    Symlinked projects need no copy-back (their ``.agents`` *is* the store).

    Transport is **not** dotagents' concern. If ``<agents_dir>/hooks/sync`` exists
    it owns that step entirely and its exit code is returned. Otherwise the bundled
    git path runs (``pull --rebase`` / commit / push) as a convenient default --
    with ``--remote`` on a non-git ``agents_dir``, ``git init`` + set ``origin``
    first, making first-time bootstrap a single command. Neither is required: a
    store that never leaves the machine is a valid setup."""
    log = _Log(logger)
    agents_dir = Path(agents_dir).expanduser().resolve()

    # Copy-back reconciles copy mode, which is dotagents' own mess to clean up:
    # `link --copy` mirrors store -> project when symlinking isn't available, so
    # the two diverge as soon as the project's copy is edited. This folds those
    # edits back. A symlinked project has no divergence and needs none of it.
    if project_dir is not None:
        project_dir = Path(project_dir).expanduser().resolve()
        target = project_dir / ".agents"
        if target.is_dir() and not os.path.islink(str(target)):
            if (target / ".git").exists():
                # Same guard as link_project's adoption: a git checkout at
                # <project>/.agents belongs to something else; copying it back
                # would swallow it (and its .git) into the store.
                log(
                    "skip copy-back: %s/.agents is itself a git checkout; not "
                    "copying it into the store",
                    project_dir.name,
                )
            else:
                store = project_store(agents_dir, resolve_name(project_dir, name), store_dir)
                log("copy-back: %s/.agents -> %s", project_dir.name, store)
                if not dry_run:
                    store.mkdir(parents=True, exist_ok=True)
                    _copy_tree(target, store, overwrite=True)

    # Everything past this point is transport, which dotagents does not own: how
    # the store reaches other machines -- git, rsync, a cloud drive, nothing at
    # all -- is the user's choice. A hook takes it over entirely; the git path
    # below is only the bundled default.
    hook_rc = _run_sync_hook(agents_dir, message=message, dry_run=dry_run, log=log)
    if hook_rc is not None:
        return hook_rc

    is_git = (agents_dir / ".git").exists()
    if not is_git:
        if remote is None:
            log(
                "WARN: %s is not a git repo, no sync hook, and no --remote given; "
                "nothing to sync. Add a hooks/sync script, or bootstrap git with "
                "`dotagents sync --remote <url>`.",
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

    # Auth for the network ops (pull/push): a direct `dotagents sync` isn't run
    # through the private-sync hook that would otherwise source the git-auth
    # bypass, so apply it here too. No-op unless DOTAGENTS_AGENTS_TOKEN is set.
    config_args, auth_env, auth_tmp = _agents_git_auth(log)
    try:
        if pull and _has_origin(agents_dir):
            log("git pull --rebase --autostash")
            res = _git(agents_dir, "pull", "--rebase", "--autostash", "origin", "HEAD",
                       capture=True, config_args=config_args, env=auth_env)
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
            res = _git(agents_dir, "push", "-u", "origin", "HEAD", capture=True,
                       config_args=config_args, env=auth_env)
            if res.returncode != 0:
                log("WARN: git push failed: %s", (res.stderr or "").strip())
    finally:
        if auth_tmp:
            try:
                os.unlink(auth_tmp)
            except OSError:
                pass
    return 0
