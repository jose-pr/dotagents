"""`dotagents launch` -- start an agent's CLI with dotagents' env and context.

    dotagents launch claude -- --model sonnet
    dotagents launch                       # the active agent (same detection as `context`)
    dotagents launch codex --dry-run       # print what would run, run nothing

What happens, in order (what the SessionStart / env-loader hooks do for a
running session, done up front here):

1. **Environment.** The full ``dotagents env`` assembly for this scope --
   identity vars (``AGENT``, ``AGENTS_HARNESS``, ...), ``AGENTS_HOME`` /
   ``AGENTS_PROJECT_ROOT`` / ``AGENTS_PYTHON``, one ``<NAME>_OVERLAY_ROOT``
   per installed overlay, the PATH / PYTHONPATH prepends, the env-file chain --
   is applied to this process and handed to the child, so the harness and
   everything it spawns see it. ``-g`` skips the project tiers (the same
   narrowed meaning as ``env`` / ``context``), never "another store".
2. **Context.** ``dotagents context`` for that agent (what the harness does
   not already load by itself). It is written to a file, exported as
   ``AGENTS_CONTEXT_FILE``, and handed to the harness the way that harness
   accepts appended system-prompt text (Claude Code:
   ``--append-system-prompt-file``; pi: ``--append-system-prompt`` on POSIX).
   A harness with no append flag gets it
   the static way instead: merged as the managed ``dotagents:context`` block
   into its own instruction file in the project (``Agent.context_target``,
   what ``context --write-agent`` does), which it then loads itself.
   ``--no-context`` skips this step; ``--inline`` also inlines the on-demand
   files the sources reference.
3. **The harness.** ``Agent.launch_command`` (``claude``, ``codex``,
   ``gemini``, ``cursor-agent``, ``copilot``, ``pi``), resolved on the PATH from step
   1 -- so a harness an overlay's ``bin/`` provides is found -- or
   ``--command`` for a program under another name. Everything after the first
   literal ``--`` is passed through untouched (duho's ``_passthrough_``
   convention), after the flags dotagents adds, so yours win where the
   harness takes the last value. The exit code is the harness's.

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


def _spawn(argv: "list[str]", env: "dict[str, str]") -> int:
    """Run ``argv`` with ``env`` on the real terminal streams and return its
    exit code. Ctrl-C reaches the child through the shared console; this
    keeps waiting for the child's real exit code instead of dying first and
    leaving an orphan under a half-torn-down parent."""
    proc = subprocess.Popen(argv, env=env)
    while True:
        try:
            return proc.wait()
        except KeyboardInterrupt:
            continue


def _describe(argv: "list[str]") -> str:
    """A command line a human can read: elements with a space (or empty)
    double-quoted. Not ``shlex.join`` -- it is not for a shell, and its
    single-quote form is wrong for cmd.exe."""
    return " ".join('"%s"' % a if (" " in a or not a) else a for a in argv)


class Launch(DotAgentsArgs):
    """Start an agent's CLI with dotagents' environment applied and its
    assembled context handed over as appended system-prompt text.

    ``dotagents launch <agent> -- <the agent's own arguments>``. ``-g`` skips
    the project tiers of the env and context walk (the ``env`` / ``context``
    meaning), it does not select another store.
    """

    _parsername_ = "launch"

    global_scope: bool = False
    "Skip the project tiers: the user store only (as `env` / `context`)."
    ("--global", "-g")

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
        env = dict(os.environ)

        # 2. Context.
        extra: "list[str]" = []
        if not self.no_context:
            text = _context.assemble_context(agent, scope, inline=self.inline)
            if text:
                context_file = _scratch_dir() / ("launch-%s-context.md" % agent.name)
                write_text_lf(context_file, text)
                env[CONTEXT_FILE_ENV] = str(context_file)
                args = agent.launch_context_args(context_file)
                if args is not None:
                    extra = list(args)
                elif agent.context_target:
                    self._logger_.info(
                        "%s takes no appended system prompt; merging the context "
                        "into %s (the managed block it loads itself)",
                        agent.name, agent.context_target,
                    )
                    agent.write_context(
                        Path(project_root), text, force=False, dry_run=self.dry_run,
                        logger=self._logger_,
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

        # 3. The harness.
        program = self.command or agent.launch_command
        if not program:
            raise SystemExit(
                "error: %s has no command-line harness dotagents knows how to start; "
                "pass --command <program>" % agent.name
            )
        exe = shutil.which(program, path=env.get("PATH"))
        if exe is None:
            if not self.dry_run:
                raise SystemExit(
                    "error: %r not found on PATH (after applying the dotagents env)" % program
                )
            self._logger_.warning("%r is not on PATH (after applying the dotagents env)", program)
            exe = program
        argv = [exe, *extra, *self._passthrough_]

        if self.dry_run:
            _write_stdout(
                "%s\nenv: %s\n" % (_describe(argv), ", ".join(sorted(changes)) or "(no changes)")
            )
            return 0
        self._logger_.debug("%s", _describe(argv))
        return _spawn(argv, env)
