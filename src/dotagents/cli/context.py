"""`dotagents context` -- assemble the effective context for agents."""

from pathlib import Path
from typing import Optional

from dotagents._fs import write_text_lf
from dotagents.cli._common import (  # noqa: F401  (_write_stdout re-exported for tests)
    DotAgentsArgs,
    _write_stdout,
    resolve_user_store,
)


class Context(DotAgentsArgs):
    """Assemble the effective context for agents.

    Roots (both configurable, never hardcoded -- D58/D79/D80): the user store is
    ``--agents-dir`` -> ``$AGENTS_HOME`` ->
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
    (
        "Merge the context into each agent's own instruction file under the "
        "project root (Claude .claude/CLAUDE.md, Codex AGENTS.md, Gemini "
        "GEMINI.md, Cursor .cursorrules, Copilot .github/copilot-instructions.md) "
        "as a managed block, instead of printing it."
    )
    ("--write-agent",)

    inline: bool = False
    (
        "Also inline the on-demand .md files the sources reference. Off by "
        "default: the base rules say to read those only when a task needs them."
    )
    ("--inline",)

    FORMATS = ("markdown", "system-reminder", "json")

    def __call__(self) -> int:
        from dotagents import _agents
        from dotagents import _context
        from dotagents import _scope
        import json
        import os

        if self.format not in self.FORMATS:
            raise SystemExit(
                "error: --format must be one of %s (got %r)"
                % (", ".join(self.FORMATS), self.format)
            )
        if self.write_agent and self.format == "json":
            raise SystemExit("error: --write-agent writes markdown; it cannot take --format json")
        if self.write_agent and self.out != "-":
            raise SystemExit("error: --write-agent and an output path are mutually exclusive")

        project_root = _scope.project_root_default()
        scope = _scope.Scope.of(
            agents_dir=resolve_user_store(self.agents_dir),
            project_root=project_root,
            global_scope=self.global_scope,
        )

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
                _context.assemble_context_data(agent, scope, inline=self.inline)
                for agent in active_agents
            ]
            out_obj = payloads[0] if len(payloads) == 1 else payloads
            blob = json.dumps(out_obj, indent=2, ensure_ascii=False)
            if self.out and self.out != "-":
                write_text_lf(self.out, blob)
                self._logger_.info("Wrote JSON context to %s", self.out)
            else:
                # default '-' -> stdout (json never writes native configs), as
                # UTF-8: a bare print() encodes with the console codepage and
                # dies on any character outside Latin-1.
                _write_stdout(blob + "\n")
            return 0

        # --- markdown / system-reminder text paths ---
        for agent in active_agents:
            text = _context.assemble_context(agent, scope, inline=self.inline)

            if self.format == "system-reminder":
                text = (
                    "<!-- system-reminder: begin -->\n"
                    + text
                    + "\n<!-- system-reminder: end -->"
                )

            if self.write_agent:
                # The PROJECT root, never the store: the target is the harness's
                # own instruction file, merged as a managed block.
                agent.write_context(project_root, text, force=False, dry_run=False, logger=self._logger_)
            elif self.out == "-":
                # Just the context on stdout. A per-agent delimiter is emitted ONLY when
                # more than one agent is generated, so a single-agent run (the default)
                # is clean, pipeable output with no decoration to strip.
                if len(active_agents) > 1:
                    _write_stdout("# --- %s ---\n" % agent.name)
                _write_stdout(text + "\n")
            else:
                write_text_lf(self.out, text)
                self._logger_.info(f"Wrote {agent.name} context to {self.out}")

        return 0
