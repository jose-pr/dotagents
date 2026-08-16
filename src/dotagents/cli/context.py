"""`dotagents context` -- assemble the effective context for agents (Plan 04)."""

import sys
from pathlib import Path
from typing import Optional

from dotagents.cli._common import DotAgentsArgs, resolve_user_store


def _write_stdout(text: str) -> None:
    """Write to stdout as UTF-8, whatever the console's encoding claims to be.

    A bare `print()` encodes with the console codepage -- cp1252 on a default
    Windows shell -- so a single character outside Latin-1 (an arrow, a box-drawing
    rule, a curly quote, any emoji) raises UnicodeEncodeError and the command dies
    having emitted nothing. Context files routinely contain such characters, and
    this is the SessionStart hook's payload, so the failure is both likely and
    silent. Write bytes through the underlying buffer instead, replacing anything
    even UTF-8 cannot represent rather than aborting.
    """
    data = text.encode("utf-8", errors="replace")
    buffer = getattr(sys.stdout, "buffer", None)
    if buffer is None:  # pragma: no cover -- captured/replaced stdout in tests
        sys.stdout.write(text)
        return
    buffer.write(data)
    buffer.flush()


class Context(DotAgentsArgs):
    """Assemble the effective context for agents (Plan 04).

    Roots (both configurable, never hardcoded -- D58/D79/D80): the user store is
    ``--agents-dir`` -> ``$AGENTS_HOME`` -> legacy ``$DOTAGENTS_AGENTS_DIR`` ->
    ``~/.agents`` (:func:`~dotagents.cli._common.resolve_user_store`), and the
    project root is ``$AGENTS_PROJECT_ROOT`` -> ``$CLAUDE_PROJECT_DIR`` -> the cwd
    (:func:`~dotagents._scope.project_root_default`). This matters most here: the
    SessionStart hook runs ``dotagents context`` from wherever the session happens
    to start, so a pinned project root is the only thing that keeps the assembled
    context stable across subdirectories.

    ``-g/--global`` means **skip the project-level context files**, NOT "resolve a
    different store" -- same narrowed meaning as ``dotagents env``'s (and unlike
    ``DotAgentsArgs.resolve_scope``, which is deliberately not used here)."""

    _parsername_ = "context"

    format: str = "markdown"
    "Output format: markdown, system-reminder, or json."
    ("--format",)

    # Inherited from `DotAgentsArgs`; help restated because this command's `-g` is
    # narrower than the base's and its store is always the user store (see the
    # matching comment in `cli/env.py`).
    global_scope: bool = False
    "Skip project-level context files (the store root is unaffected)."
    ("--global", "-g")

    agents_dir: "Optional[Path]" = None
    "User store root override (default: $AGENTS_HOME, else ~/.agents)."
    ("--agents-dir",)

    agents: "list[str]" = []
    "List of agents to generate context for (e.g. claude,gemini). Default: active agent."
    ("--agents",)

    out: str = "-"
    "Output path (positional). Default '-' = stdout; a path writes that file; use "
    "--write-agent to write each agent's native config file instead."
    ("out",)

    write_agent: bool = False
    "Write each agent's native config file (e.g. Claude's CONTEXT.md) instead of a path."
    ("--write-agent",)

    def __call__(self) -> int:
        from dotagents import _agents
        from dotagents import _context
        from dotagents import _scope
        import json
        import os

        project_root = _scope.project_root_default()
        agents_dir = resolve_user_store(self.agents_dir)

        agent_names = []
        if self.agents:
            for a in self.agents:
                agent_names.extend([x.strip() for x in a.split(",") if x.strip()])

        if agent_names:
            active_agents = []
            for name in agent_names:
                a = _agents.get_agent(name)
                if a:
                    active_agents.append(a)
                else:
                    self._logger_.warning("Unknown agent: %s", name)
        else:
            # Default target = the active agent (env-var detection / $AGENTS_HARNESS
            # stamp / config-file detect), not "all detected".
            active_agents = [
                _agents.resolve_active_agent(os.environ, root=project_root)
            ]

        # --- JSON: emit structured data (object for one agent, array for many);
        #     never writes native config files. ---
        if self.format == "json":
            payloads = [
                _context.assemble_context_data(
                    agent, agents_dir, project_root, global_scope=self.global_scope
                )
                for agent in active_agents
            ]
            out_obj = payloads[0] if len(payloads) == 1 else payloads
            blob = json.dumps(out_obj, indent=2, ensure_ascii=False)
            if self.out and self.out != "-":
                Path(self.out).write_text(blob, encoding="utf-8")
                self._logger_.info("Wrote JSON context to %s", self.out)
            else:
                # default '-' -> stdout (json never writes native configs). Same
                # cp1252-crash risk as the markdown path below -- a bare print()
                # encodes with the console codepage and dies on any character
                # outside Latin-1 (confirmed live: a `≥` in real assembled
                # context crashed this exact line before the fix).
                _write_stdout(blob + "\n")
            return 0

        # --- markdown / system-reminder text paths ---
        for agent in active_agents:
            text = _context.assemble_context(
                agent, agents_dir, project_root, global_scope=self.global_scope
            )

            if self.format == "system-reminder":
                text = (
                    "<!-- system-reminder: begin -->\n"
                    + text
                    + "\n<!-- system-reminder: end -->"
                )

            if self.write_agent:
                agent.write_context(agents_dir, text, force=False, dry_run=False, logger=self._logger_)
            elif self.out == "-":
                # Just the context on stdout. A per-agent delimiter is emitted ONLY when
                # more than one agent is generated, so a single-agent run (the default)
                # is clean, pipeable output with no decoration to strip.
                if len(active_agents) > 1:
                    _write_stdout("# --- %s ---\n" % agent.name)
                _write_stdout(text + "\n")
            else:
                Path(self.out).write_text(text, encoding="utf-8")
                self._logger_.info(f"Wrote {agent.name} context to {self.out}")

        return 0
