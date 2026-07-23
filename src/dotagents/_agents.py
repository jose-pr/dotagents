"""Agent registry: base Agent type + per-agent adapters (Plan 00)."""

from __future__ import annotations

from pathlib import Path
from typing import Optional


class Agent:
    """Base class for dotagents adapters.

    Identity (plan 08): each adapter declares its ``harness_id`` (the stable
    ecosystem name, e.g. ``claude-code`` -- NOT the short registry ``name``),
    ``vendor`` (provider family), and ``model_source_vars`` (the vendor-native
    env vars to read a running model id from, in precedence order). These feed
    ``stamp_identity`` which emits the standardized ``AGENTS_*``/``AGENT`` vars.
    """

    name: str = ""
    context_files: list[str] = []
    harness_loads: list[str] = []
    detect_env_vars: list[str] = []

    # --- identity mapping (plan 08) ------------------------------------
    harness_id: str = ""
    vendor: str = ""
    model_source_vars: list[str] = []

    def detect_env(self, environ: dict[str, str]) -> bool:
        """Return True if this agent's harness is running based on env vars.

        Default: True if any declared marker var is present. Adapters whose
        markers are prefixes (e.g. Codex's ``CODEX_SANDBOX_*``) override this.
        """
        return any(var in environ for var in self.detect_env_vars)

    def resolve_model(self, environ: dict[str, str]) -> "Optional[str]":
        """Return the running model id from the first populated source var, or None."""
        for var in self.model_source_vars:
            val = environ.get(var)
            if val:
                return val
        return None

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
    # Confirmed markers (code.claude.com/docs/en/env-vars): CLAUDECODE=1 plus the
    # CLAUDE_CODE_* family (CLAUDE_CODE_ENTRYPOINT, ...).
    detect_env_vars = ["CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT"]
    harness_id = "claude-code"
    vendor = "anthropic"
    model_source_vars = ["ANTHROPIC_MODEL"]

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
    # Confirmed marker: GEMINI_CLI=1, set by Gemini CLI in every child process it
    # spawns (google-gemini.github.io/gemini-cli, run_shell_command docs). The old
    # GEMINI_SESSION was invented; GEMINI_API_KEY is a credential, not a marker.
    detect_env_vars = ["GEMINI_CLI"]
    harness_id = "gemini-cli"
    vendor = "google"
    model_source_vars = ["GEMINI_MODEL"]

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
    # Codex ships NO dedicated runtime marker (openai/codex). The old CODEX_SESSION
    # was invented. Detect via the state dir CODEX_HOME or the sandbox signal vars
    # Codex sets for child processes (CODEX_SANDBOX, CODEX_SANDBOX_NETWORK_DISABLED,
    # any CODEX_SANDBOX_* -- matched by prefix in detect_env below).
    detect_env_vars = ["CODEX_HOME", "CODEX_SANDBOX"]
    harness_id = "codex"
    vendor = "openai"
    # OpenAI base/model live under OPENAI_* (support the OPENAI_API_BASE alias
    # elsewhere); Codex has no dedicated model var, so read the OpenAI one.
    model_source_vars = ["OPENAI_MODEL"]

    def detect_env(self, environ: "dict[str, str]") -> bool:
        # Exact markers plus any CODEX_SANDBOX_* variant (prefix match).
        if any(var in environ for var in self.detect_env_vars):
            return True
        return any(k.startswith("CODEX_SANDBOX") for k in environ)

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
    # Intended marker: CURSOR_AGENT=1 (cursor.com/docs/cli). Note a known bug where
    # it is not always propagated to spawned bash (forum.cursor.com/t/.../132427),
    # so config-file detect() remains the fallback. The old CURSOR_SESSION_ID was
    # invented.
    detect_env_vars = ["CURSOR_AGENT"]
    harness_id = "cursor"
    vendor = "cursor"
    model_source_vars = ["CURSOR_DEFAULT_MODEL"]

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
    # Copilot ships NO runtime marker var yet -- the request for one (e.g.
    # COPILOT_AGENT) is still open (microsoft/vscode#311734). COPILOT_MODEL /
    # COPILOT_HOME exist but are config, not reliable "am I running" markers, so
    # they are NOT used for detection; detection falls back to config-file
    # detect(). The old COPILOT_SESSION_ID / GITHUB_COPILOT were invented.
    detect_env_vars = []
    harness_id = "copilot"
    vendor = "github"
    model_source_vars = ["COPILOT_MODEL"]

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

def _harness_alias(value: str) -> "Optional[str]":
    """Map an $AGENTS_HARNESS value (registry name OR harness_id) to a name."""
    if value in _REGISTRY:
        return value
    for name, cls in _REGISTRY.items():
        if cls.harness_id == value:
            return name
    return None


def resolve_active_agent(
    environ: dict[str, str],
    explicit: Optional[str] = None,
    root: Optional[Path] = None,
) -> Agent:
    """Resolve the active agent by precedence (plan 00).

    Precedence: explicit (--agents) > $AGENTS_HARNESS (registry name or
    harness_id) > env-var detection (detect_env) > config-file detection
    (detect(root)) > default (claude).

    ``root`` is where config-file detect() looks (default: cwd).
    """
    if explicit and explicit in _REGISTRY:
        return _REGISTRY[explicit]()

    stamped = environ.get("AGENTS_HARNESS")
    if stamped:
        alias = _harness_alias(stamped)
        if alias:
            return _REGISTRY[alias]()

    for name, cls in _REGISTRY.items():
        agent = cls()
        if agent.detect_env(environ):
            return agent

    # Config-file fallback: which agent's config is present in the tree?
    detect_root = root if root is not None else Path.cwd()
    for name, cls in _REGISTRY.items():
        agent = cls()
        try:
            if agent.detect(detect_root):
                return agent
        except OSError:
            pass

    return ClaudeAgent()


def stamp_identity(
    environ: dict[str, str],
    explicit: Optional[str] = None,
    root: Optional[Path] = None,
) -> dict[str, str]:
    """Return the standardized ``AGENTS_*`` / ``AGENT`` identity vars (plan 08).

    Emits ``AGENTS_HARNESS`` (the harness_id, e.g. ``claude-code`` -- NOT the
    short name), ``AGENTS_VENDOR``, ``AGENT`` (= AGENTS_HARNESS, aligning with
    the emerging ecosystem marker used by Goose/Amp; agentsmd/agents.md#136),
    and, when derivable, ``AGENTS_MODEL`` (from the adapter's vendor model var)
    and ``AGENTS_AGENT`` (a named persona, from $AGENTS_AGENT if already set).

    Never emits a var it cannot source (no empty ``AGENTS_MODEL=``). Does NOT
    clobber a value already set in ``environ`` -- an explicit user/harness value
    wins. The deliberate curated mapping replaces the precursor's blanket
    ``CLAUDE_*``->``AGENTS_*`` rewrite (no ``AGENTS_CODE_SESSION_ID`` junk).
    """
    active = resolve_active_agent(environ, explicit=explicit, root=root)

    identity: dict[str, str] = {}

    def _set(key: str, value: "Optional[str]") -> None:
        if value and not environ.get(key):
            identity[key] = value

    _set("AGENTS_HARNESS", active.harness_id or active.name)
    _set("AGENTS_VENDOR", active.vendor)
    _set("AGENT", active.harness_id or active.name)
    _set("AGENTS_MODEL", active.resolve_model(environ))
    # A named persona (~/.agents/<agent>.md); only surface one already selected.
    _set("AGENTS_AGENT", environ.get("AGENTS_AGENT"))

    return identity
