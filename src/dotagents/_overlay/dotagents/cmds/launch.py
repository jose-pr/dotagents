"""`dotagents launch` -- start an agent's CLI with dotagents' env and context.

    dotagents launch claude -- --model sonnet
    dotagents launch                       # the active agent (same detection as `context`)
    dotagents launch codex --dry-run       # print what would run, run nothing

What happens, in order (what the SessionStart / env-loader hooks do for a
running session, done up front here):

1. **Environment.** The full ``dotagents env`` assembly for this scope --
   identity vars (``AGENT``, ``AGENTS_HARNESS``, ...), ``AGENTS_HOME`` /
   ``AGENTS_PROJECT_ROOT`` / ``AGENTS_PYTHON``, one ``<NAME>_OVERLAY_ROOT``
   per installed overlay, the PATH prepend and ``AGENTS_PYTHONPATH``, the env-file chain --
   is applied to this process and handed to the child, so the harness and
   everything it spawns see it; a variable a layer unset is removed from
   both. ``-g`` skips the project tiers (the same narrowed meaning as
   ``env`` / ``context``), never "another store".
2. **Context.** ``dotagents context`` for that agent (what the harness does
   not already load by itself). It is written to a file (one per agent and
   project under ``<user store>/.cache/launch/``, overwritten by the next
   launch), exported as ``AGENTS_CONTEXT_FILE``, and handed to the harness
   the way that harness accepts appended system-prompt text (Claude Code:
   ``--append-system-prompt-file``; pi: ``--append-system-prompt`` on POSIX).
   A harness with no append flag gets it
   the static way instead: merged as the managed ``dotagents:context`` block
   into its own instruction file in the project (``Agent.context_target``,
   what ``context --write-agent`` does), which it then loads itself.
   ``--no-context`` skips this step; ``--inline`` also inlines the on-demand
   files the sources reference.
3. **The harness.** ``Agent.launch_command`` (``claude``, ``codex``,
   ``gemini``, ``cursor-agent``, ``copilot``, ``pi``), resolved on the PATH from step
   1 -- so a harness an overlay's ``bin/`` provides is found; never in the
   current directory, which Windows would otherwise search first -- or
   ``--command`` for a program under another name. Everything after the first
   literal ``--`` is passed through untouched (duho's ``_passthrough_``
   convention), after the flags dotagents adds, so yours win where the
   harness takes the last value. The exit code is the harness's (``128 + N``
   when a signal N killed it). On Windows a harness that is a ``.cmd`` /
   ``.bat`` shim runs through cmd.exe, which re-parses its arguments, so an
   argument containing ``& | < > ^ % ! "`` or a newline is refused.

The command is bundled with dotagents (discovered from the package, like
``findings``); a same-named ``launch.py`` in a scope's ``dotagents/cmds/``
overrides it.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from dotagents.cli import DotAgentsArgs, _write_stdout

#: Exported to the child: where the assembled context was written.
CONTEXT_FILE_ENV = "AGENTS_CONTEXT_FILE"


#: Characters cmd.exe acts on inside a `.cmd` / `.bat` command line even where
#: `subprocess.list2cmdline` does not quote (`&|<>^`), expands in any case
#: (`%`, and `!` under delayed expansion), or that end or break the quoting.
_CMD_EXE_UNSAFE = frozenset('&|<>^%!"\r\n')


def _spawn(argv: "list[str]", env: "dict[str, str]") -> int:
    """Run ``argv`` with ``env`` on the real terminal streams and return its
    exit code. Ctrl-C reaches the child through the shared console; this
    keeps waiting for the child's real exit code instead of dying first and
    leaving an orphan under a half-torn-down parent.

    A child killed by signal N reports ``-N`` (POSIX); that is returned as the
    shell's ``128 + N``, since a negative exit status would be truncated to
    ``256 - N`` on the way out."""
    proc = subprocess.Popen(argv, env=env)
    while True:
        try:
            rc = proc.wait()
        except KeyboardInterrupt:
            continue
        return 128 - rc if rc < 0 else rc


def _via_cmd_exe(exe: str) -> bool:
    """Whether Windows runs ``exe`` through ``cmd.exe /c`` (a ``.cmd`` / ``.bat``
    file: the npm shims for claude, codex, gemini, copilot and pi)."""
    return os.name == "nt" and Path(exe).suffix.lower() in (".cmd", ".bat")


def _refuse_cmd_exe_metachars(exe: str, args: "list[str]") -> None:
    """cmd.exe re-parses a batch file's command line: an unquoted ``&`` starts
    a second command and ``%VAR%`` is expanded inside any argument, and no
    escaping of ``%`` survives ``cmd /c`` reliably. So an argument carrying one
    of those characters is refused rather than handed over altered."""
    for arg in args:
        bad = sorted(set(arg) & _CMD_EXE_UNSAFE)
        if bad:
            raise SystemExit(
                "error: %s is a batch file that cmd.exe re-parses, and the argument "
                "%r contains %s, which cmd.exe would act on. Put the text in a file "
                "the harness reads, or pass --command with the harness's own "
                "executable (e.g. node and its script)."
                % (exe, arg, " ".join(repr(c) for c in bad))
            )


def _which(program: str, path: "Optional[str]", pathext: "Optional[str]" = None) -> "Optional[str]":
    """``program`` resolved on ``path`` -- the PATH ONLY, never the current
    directory.

    ``shutil.which(program, path=...)`` on Windows before Python 3.12 searches
    the current directory first even when ``path`` is given, so a ``claude.cmd``
    at the root of the repository being worked on ran instead of the real
    harness, with the user's full environment. On Windows the PATH entries are
    walked here, each against ``PATHEXT``, skipping empty, ``.`` and other
    relative entries (all of them name the current directory or a directory
    under it). A ``program`` with a directory part is the caller's explicit
    choice and goes to ``shutil.which`` as is; POSIX has no implicit current
    directory and uses ``shutil.which`` too."""
    if os.name != "nt" or os.path.dirname(program):
        return shutil.which(program, path=path)
    exts = [e for e in (pathext or ".COM;.EXE;.BAT;.CMD").split(os.pathsep) if e]
    if os.path.splitext(program)[1].lower() in (e.lower() for e in exts):
        names = [program]
    else:
        names = [program + ext for ext in exts]
    for entry in (path or "").split(os.pathsep):
        entry = entry.strip().strip('"')
        if not entry or not os.path.isabs(entry):
            continue
        for name in names:
            candidate = os.path.join(entry, name)
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return candidate
    return None


def _git_tracks(root: Path, rel: str) -> bool:
    """Whether the git repository at ``root`` tracks ``rel`` (False when there
    is no repository or no git)."""
    try:
        res = subprocess.run(
            ["git", "-C", str(root), "ls-files", "--error-unmatch", "--", rel],
            capture_output=True, text=True,
        )
    except OSError:
        return False
    return res.returncode == 0


def _describe(argv: "list[str]") -> str:
    """A command line a human can read: elements with a space (or empty)
    double-quoted. Not ``shlex.join`` -- it is not for a shell, and its
    single-quote form is wrong for cmd.exe."""
    return " ".join('"%s"' % a if (" " in a or not a) else a for a in argv)


class Launch(DotAgentsArgs):
    """Start an agent's CLI with dotagents' environment and context applied.

    The assembled context is handed over as appended system-prompt text.
    ``dotagents launch <agent> -- <the agent's own arguments>``. ``-g`` skips
    the project tiers of the env and context walk (the ``env`` / ``context``
    meaning), it does not select another store.
    """

    _parsername_ = "launch"

    global_scope: bool = False
    "Skip the project tiers: the user store only (as `env` / `context`)."
    ("--global", "-g")

    # Restated from `DotAgentsArgs` for its help only (same flag and default):
    # here the store is always the user store, as for `env` / `context`.
    agents_dir: Optional[Path] = None
    "User store root override (default: $AGENTS_HOME, else ~/.agents)."
    ("--agents-dir",)

    agent: Optional[str] = None
    (
        "Which agent to start: claude, codex, gemini, cursor, copilot, pi. Default: "
        "the active agent (explicit > $AGENTS_HARNESS > env detection > "
        "config files > claude)."
    )
    ("agent",)

    command: Optional[str] = None
    (
        "The program to run instead of the agent's own (a path, or a name "
        "resolved on the exported PATH); the context flag is still the agent's."
    )
    ("--command",)

    no_context: bool = False
    "Do not assemble or hand over the context; environment only."
    ("--no-context",)

    inline: bool = False
    "Also inline the on-demand .md files the context sources reference."
    ("--inline",)

    write_agent: bool = False
    (
        "For an agent with no append flag, merge the context into its own "
        "instruction file even when git tracks it or -g is set."
    )
    ("--write-agent",)

    dry_run: bool = False
    "Print the command line and the names of the exported changes; run nothing."
    ("--dry-run",)

    def __call__(self) -> int:
        from dotagents import _agents, _context, _env, _scope
        from dotagents._fs import write_text_lf
        from dotagents.cli._common import _scratch_dir, resolve_user_store

        project_root = _scope.project_root_default()
        scope = _scope.Scope.of(
            agents_dir=resolve_user_store(self.agents_dir),
            project_root=project_root,
            global_scope=self.global_scope,
        )

        if self.agent:
            agent = _agents.get_agent(self.agent)
            if agent is None:
                raise SystemExit(
                    "error: unknown agent %r (known: %s)"
                    % (self.agent, ", ".join(a.name for a in _agents.get_all_agents()))
                )
        else:
            agent = _agents.resolve_active_agent(os.environ, root=project_root)
            self._logger_.info("active agent is %s", agent.name)

        # 1. Environment: applied here too, not only to the child -- the
        # context step and anything else this process does should see the
        # same world the harness will.
        changes = _env.get_environment(
            scope, base_env=dict(os.environ), explicit=agent.name, logger=self._logger_
        )
        os.environ.update(changes)
        # A layer that UNSET a variable (an env.py printing null, `unset` in a
        # plain env file) means the harness must not inherit it either.
        for key in getattr(changes, "removed", ()):
            os.environ.pop(key, None)
        env = dict(os.environ)

        # 2. The harness, resolved before anything is written: a launch that
        # fails with "not found" must leave the project untouched.
        program = self.command or agent.launch_command
        if not program:
            raise SystemExit(
                "error: %s has no command-line harness dotagents knows how to start; "
                "pass --command <program>" % agent.name
            )
        exe = _which(program, env.get("PATH"), env.get("PATHEXT"))
        if exe is None:
            if not self.dry_run:
                raise SystemExit(
                    "error: %r not found on PATH (after applying the dotagents env)" % program
                )
            self._logger_.warning("%r is not on PATH (after applying the dotagents env)", program)
            exe = program

        # 3. Context.
        extra: "list[str]" = []
        if not self.no_context:
            text = _context.assemble_context(agent, scope, inline=self.inline)
            if text:
                if self.dry_run:  # removed at exit: a dry run leaves nothing behind
                    context_file = _scratch_dir() / ("launch-%s-context.md" % agent.name)
                else:
                    context_file = self._context_file(scope, agent.name, project_root)
                write_text_lf(context_file, text)
                env[CONTEXT_FILE_ENV] = str(context_file)
                args = agent.launch_context_args(context_file)
                if args is not None:
                    extra = list(args)
                elif agent.context_target and self._may_write(agent, project_root):
                    self._logger_.info(
                        "%s takes no appended system prompt; merging the context "
                        "into %s (the managed block it loads itself)",
                        agent.name, agent.context_target,
                    )
                    # The project file may be committed: keep `$<NAME>_OVERLAY_ROOT`
                    # as written rather than baking in this machine's paths.
                    agent.write_context(
                        Path(project_root),
                        _context.assemble_context(
                            agent, scope, inline=self.inline, expand_vars=False
                        ),
                        dry_run=self.dry_run, logger=self._logger_,
                    )
                else:
                    self._logger_.warning(
                        "%s has no way to take the context; it is at $%s only",
                        agent.name, CONTEXT_FILE_ENV,
                    )
            else:
                self._logger_.info(
                    "nothing to hand over -- %s already loads every source", agent.name
                )

        argv = [exe, *extra, *self._passthrough_]
        if _via_cmd_exe(exe):
            _refuse_cmd_exe_metachars(exe, argv[1:])

        if self.dry_run:
            removed = sorted(getattr(changes, "removed", ()))
            _write_stdout(
                "%s\nenv: %s\n%s" % (
                    _describe(argv), ", ".join(sorted(changes)) or "(no changes)",
                    "unset: %s\n" % ", ".join(removed) if removed else "",
                )
            )
            return 0
        self._logger_.debug("%s", _describe(argv))
        return _spawn(argv, env)

    @staticmethod
    def _context_file(scope, agent_name: str, project_root) -> Path:
        """Where a launch writes its context: one stable file per agent and
        project (one for ``-g``) under ``<user store>/.cache/launch/``, overwritten
        by the next launch. Not the per-process temp dir: that is removed at
        exit, and a session ended by SIGTERM / SIGHUP or a closed console
        never runs its exit handlers, which left one directory per session in
        TEMP. Keyed by project so two concurrent launches in different
        projects do not hand each other their context."""
        import hashlib

        key = "-g" if scope.global_scope else str(Path(project_root).resolve())
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]
        cache = Path(scope.user_root) / ".cache" / "launch"
        cache.mkdir(parents=True, exist_ok=True)
        ignore = cache / ".gitignore"
        if not ignore.exists():  # a store kept in git: never commit these
            ignore.write_bytes(b"*\n")
        return cache / ("%s-%s.md" % (agent_name, digest))

    def _may_write(self, agent, project_root) -> bool:
        """Whether the context may be merged into ``agent.context_target``.
        That file is often committed (AGENTS.md, GEMINI.md, .cursorrules,
        copilot-instructions.md), and the context carries the user store's
        private rules -- so not into a file git tracks, and not under ``-g``
        (user store only), unless ``--write-agent`` asks for it."""
        if self.write_agent:
            return True
        if self.global_scope:
            reason = "-g"
        elif _git_tracks(Path(project_root), agent.context_target):
            reason = "git tracks %s" % agent.context_target
        else:
            return True
        self._logger_.warning(
            "%s takes no appended system prompt, and the context is not merged into "
            "%s (%s): it is at $%s only. Pass --write-agent to merge it anyway.",
            agent.name, agent.context_target, reason, CONTEXT_FILE_ENV,
        )
        return False
