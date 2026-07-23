"""Agent registry: base Agent type + per-agent adapters (Plan 00)."""

from __future__ import annotations

import os
import importlib
import pkgutil
from pathlib import Path
from typing import Optional


class Agent:
    """Base class for dotagents adapters."""

    name: str = ""
    context_files: list[str] = []
    harness_loads: list[str] = []
    detect_env_vars: list[str] = []

    def detect_env(self, environ: dict[str, str]) -> bool:
        """Return True if this agent's harness is running based on env vars."""
        return any(var in environ for var in self.detect_env_vars)

    def write_base_config(
        self, dest: Path, src: Path, base_agents_text: str, *, force: bool, dry_run: bool, logger
    ) -> None:
        """Write the base configuration files for this agent (used by init/install)."""
        pass

    def write_context(self, dest: Path, effective_context: str, *, force: bool, dry_run: bool, logger) -> None:
        """Write the assembled context file for this agent (used by context generator)."""
        pass

    def wire_hooks(self, dest: Path, *, dry_run: bool, logger) -> None:
        """Wire up hooks in the agent's settings if supported."""
        pass

    def detect(self, root: Path) -> bool:
        """Return True if this agent's config is present in the given root."""
        return any((root / f).exists() for f in self.context_files)


class ClaudeAgent(Agent):
    name = "claude"
    context_files = ["CLAUDE.md"]
    # Claude's harness loads ~/.agents/AGENTS.md (via @-include) and any per-dir AGENTS.md
    harness_loads = ["~/.agents/AGENTS.md", "AGENTS.md"]
    detect_env_vars = ["CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT"]

    def write_base_config(self, dest: Path, src: Path, base_agents_text: str, *, force: bool, dry_run: bool, logger) -> None:
        from dotagents._merge import merge_block, merge_claude_md, timestamped_backup_root
        
        backup_root = timestamped_backup_root(dest) if force else None

        branch = merge_block(
            dest / "AGENTS.md",
            base_agents_text,
            force=force, dry_run=dry_run, backup_root=backup_root,
        )
        if logger: logger.info("%s: AGENTS.md", branch)

        claude_md_src = src / "CLAUDE.md"
        if claude_md_src.exists():
            branch = merge_claude_md(
                dest / "CLAUDE.md",
                claude_md_src.read_text(encoding="utf-8"),
                force=force, dry_run=dry_run, backup_root=backup_root,
            )
            if logger: logger.info("%s: CLAUDE.md", branch)

    def write_context(self, dest: Path, effective_context: str, *, force: bool, dry_run: bool, logger) -> None:
        target = dest / "CONTEXT.md"
        if not dry_run:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(effective_context, encoding="utf-8")
        if logger: logger.info("wrote context to %s", target)


class GeminiAgent(Agent):
    name = "gemini"
    context_files = ["GEMINI.md"]
    harness_loads = ["GEMINI.md"]
    detect_env_vars = ["GEMINI_API_KEY", "GEMINI_SESSION"]

    def write_base_config(self, dest: Path, src: Path, base_agents_text: str, *, force: bool, dry_run: bool, logger) -> None:
        from dotagents._merge import merge_block, timestamped_backup_root
        backup_root = timestamped_backup_root(dest) if force else None
        branch = merge_block(
            dest / "GEMINI.md",
            base_agents_text,
            force=force, dry_run=dry_run, backup_root=backup_root,
        )
        if logger: logger.info("%s: GEMINI.md", branch)

    def write_context(self, dest: Path, effective_context: str, *, force: bool, dry_run: bool, logger) -> None:
        target = dest / "GEMINI.md"
        if not dry_run:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(effective_context, encoding="utf-8")
        if logger: logger.info("wrote context to %s", target)


class CodexAgent(Agent):
    name = "codex"
    context_files = ["AGENTS.md"]
    harness_loads = ["AGENTS.md"]
    detect_env_vars = ["CODEX_SESSION"]

    def write_base_config(self, dest: Path, src: Path, base_agents_text: str, *, force: bool, dry_run: bool, logger) -> None:
        from dotagents._merge import merge_block, timestamped_backup_root
        backup_root = timestamped_backup_root(dest) if force else None
        branch = merge_block(
            dest / "AGENTS.md",
            base_agents_text,
            force=force, dry_run=dry_run, backup_root=backup_root,
        )
        if logger: logger.info("%s: AGENTS.md (Codex)", branch)

    def write_context(self, dest: Path, effective_context: str, *, force: bool, dry_run: bool, logger) -> None:
        target = dest / "AGENTS.md"
        if not dry_run:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(effective_context, encoding="utf-8")
        if logger: logger.info("wrote context to %s", target)


class CursorAgent(Agent):
    name = "cursor"
    context_files = [".cursorrules", ".cursor/rules/"]
    harness_loads = [".cursorrules", ".cursor/rules/"]
    detect_env_vars = ["CURSOR_SESSION_ID"]

    def write_base_config(self, dest: Path, src: Path, base_agents_text: str, *, force: bool, dry_run: bool, logger) -> None:
        from dotagents._merge import merge_block, timestamped_backup_root
        backup_root = timestamped_backup_root(dest) if force else None
        branch = merge_block(
            dest / ".cursorrules",
            base_agents_text,
            force=force, dry_run=dry_run, backup_root=backup_root,
        )
        if logger: logger.info("%s: .cursorrules", branch)

    def write_context(self, dest: Path, effective_context: str, *, force: bool, dry_run: bool, logger) -> None:
        target = dest / ".cursorrules"
        if not dry_run:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(effective_context, encoding="utf-8")
        if logger: logger.info("wrote context to %s", target)


class CopilotAgent(Agent):
    name = "copilot"
    context_files = [".github/copilot-instructions.md"]
    harness_loads = [".github/copilot-instructions.md"]
    detect_env_vars = ["COPILOT_SESSION_ID", "GITHUB_COPILOT"]

    def write_base_config(self, dest: Path, src: Path, base_agents_text: str, *, force: bool, dry_run: bool, logger) -> None:
        from dotagents._merge import merge_block, timestamped_backup_root
        backup_root = timestamped_backup_root(dest) if force else None
        target = dest / ".github" / "copilot-instructions.md"
        branch = merge_block(
            target,
            base_agents_text,
            force=force, dry_run=dry_run, backup_root=backup_root,
        )
        if logger: logger.info("%s: %s", branch, target.relative_to(dest))

    def write_context(self, dest: Path, effective_context: str, *, force: bool, dry_run: bool, logger) -> None:
        target = dest / ".github" / "copilot-instructions.md"
        if not dry_run:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(effective_context, encoding="utf-8")
        if logger: logger.info("wrote context to %s", target)


# Registry of agents
_REGISTRY: dict[str, type[Agent]] = {
    "claude": ClaudeAgent,
    "gemini": GeminiAgent,
    "codex": CodexAgent,
    "cursor": CursorAgent,
    "copilot": CopilotAgent,
}

def get_agent(name: str) -> Optional[Agent]:
    cls = _REGISTRY.get(name)
    return cls() if cls else None

def get_all_agents() -> list[Agent]:
    return [cls() for cls in _REGISTRY.values()]

def resolve_active_agent(environ: dict[str, str], explicit: Optional[str] = None) -> Agent:
    """Resolve the active agent based on precedence.
    Precedence: --agents explicit > $AGENTS_HARNESS > detect_env > detect > claude (default)
    """
    if explicit and explicit in _REGISTRY:
        return _REGISTRY[explicit]()
    
    if "AGENTS_HARNESS" in environ and environ["AGENTS_HARNESS"] in _REGISTRY:
        return _REGISTRY[environ["AGENTS_HARNESS"]]()

    for name, cls in _REGISTRY.items():
        agent = cls()
        if agent.detect_env(environ):
            return agent

    return ClaudeAgent()

def stamp_identity(environ: dict[str, str]) -> dict[str, str]:
    """Return AGENTS_* identity vars based on the detected agent."""
    active = resolve_active_agent(environ)
    identity = {
        "AGENTS_HARNESS": active.name,
    }
    # More identity stamping from Plan 08 can be added here
    return identity
