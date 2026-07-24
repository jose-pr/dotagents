"""`dotagents context` -- assemble the effective context for agents (Plan 04)."""

from pathlib import Path
from typing import Optional

from duho import Cmd, LoggingArgs


class Context(LoggingArgs, Cmd):
    """Assemble the effective context for agents (Plan 04)."""

    _parsername_ = "context"

    format: str = "markdown"
    "Output format: markdown, system-reminder, or json."
    ("--format",)

    global_scope: bool = False
    "Use global scope."
    ("--global", "-g")

    agents: "list[str]" = []
    "List of agents to generate context for (e.g. claude,gemini). Default: active agent."
    ("--agents",)

    out: Optional[str] = None
    "Output path, or '-' for stdout. Default: agent native config file."
    ("--out", "-o")

    def __call__(self) -> int:
        from dotagents import _agents
        from dotagents import _context
        import json
        import os

        project_root = Path.cwd()
        agents_dir = Path.home() / ".agents"

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
                print(blob)
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

            if self.out == "-":
                print(f"--- Context for {agent.name} ---\n{text}\n")
            elif self.out:
                Path(self.out).write_text(text, encoding="utf-8")
                self._logger_.info(f"Wrote {agent.name} context to {self.out}")
            else:
                agent.write_context(agents_dir, text, force=False, dry_run=False, logger=self._logger_)

        return 0
