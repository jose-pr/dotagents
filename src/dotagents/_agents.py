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

    def wire_hooks(
        self, dest: Path, *, dry_run: bool, logger, config_root: "Optional[Path]" = None
    ) -> None:
        """Wire up hooks in the agent's settings, if this adapter supports it.

        A no-op on the base class: the hook schema is harness-specific, so only
        adapters with a *verified* schema override this. `config_root` overrides
        the agent's own config dir (e.g. `~/.claude`) and exists so tests can
        redirect writes away from the real one.
        """
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

    # --- hooks ---------------------------------------------------------
    #
    # SessionStart does two things:
    #
    # 1. `dotagents env` output is appended to $CLAUDE_ENV_FILE -- a real, documented
    #    variable (code.claude.com/docs/en/env-vars + hooks.md "Persist environment
    #    variables"). Claude sources that file before each Bash command, so exports
    #    land in every subsequent command of the session. It is provided to
    #    SessionStart/Setup/CwdChanged/FileChanged hooks ONLY, and only inside the
    #    hook process -- it is absent from the session's own environment, so
    #    `env | grep CLAUDE_ENV_FILE` in a normal shell proves nothing.
    #
    #    Two details the docs are explicit about, both load-bearing:
    #      * APPEND (`>>`), never truncate -- other hooks write to the same file and
    #        `>` would silently discard their variables.
    #      * Guard on non-empty (`[ -n "$CLAUDE_ENV_FILE" ]`) -- when unset (a hook
    #        type without access), `>> ""` would create a file literally named "".
    #    `--diff` keeps it to the change set rather than re-exporting the world.
    #
    # 2. `dotagents context` runs after it; Claude injects a SessionStart hook's
    #    **stdout into the session context**, which is how the assembled context
    #    reaches the model without the user remembering to run anything.
    #
    # Both halves are one `type: command` hook: SessionStart supports only
    # command/mcp_tool hooks, and ordering matters (env first, so context sees it).
    SESSION_START_COMMAND = (
        'if [ -n "$CLAUDE_ENV_FILE" ]; then '
        "dotagents env --diff --format export >> \"$CLAUDE_ENV_FILE\"; "
        "fi; "
        "dotagents context"
    )
    CWD_CHANGED_COMMAND = "[ -f AGENTS.md ] && cat AGENTS.md || true"

    def wire_hooks(
        self, dest: Path, *, dry_run: bool, logger, config_root: "Optional[Path]" = None
    ) -> None:
        """Link the shared skills dir into `~/.claude` and merge our two hooks.

        Additive and idempotent: unrelated settings keys and foreign hooks survive,
        and a second run writes nothing.
        """
        from dotagents import _hooks, _skills

        root = Path(config_root) if config_root else Path.home() / ".claude"

        # 1. Skills last mile. Publishing into `<scope>/skills/` only helps if the
        #    agent reads that dir; without this link it never does.
        shared_skills = Path(dest) / "skills"
        if not shared_skills.is_dir():
            if logger:
                logger.info("no skills to link (%s absent)", shared_skills)
        elif dry_run:
            if logger:
                logger.info("would link skills: %s -> %s", shared_skills, root / "skills")
        else:
            result = _skills.sync_path(shared_skills, root / "skills", prefer_symlink=True)
            if not result.success:
                if logger:
                    logger.warning("skills not linked: %s", result.message)
            else:
                if logger:
                    logger.info("skills (%s): %s", result.mode, result.message)
                if result.mode == "copy" and logger:
                    logger.warning(
                        "skills were COPIED, not symlinked (no symlink support here): "
                        "the copy is a point-in-time snapshot and goes stale when overlay "
                        "skills change -- re-run `dotagents init` to refresh it"
                    )

        # 2. Hooks.
        settings_path = root / "settings.json"
        settings = _hooks.load_settings(settings_path)
        hooks = settings.get("hooks")
        if not isinstance(hooks, dict):
            hooks = {}
        changed = False

        # Legacy lowercase key from the precursor -- fold it in, then drop it.
        legacy = hooks.pop("session_start", None)
        if legacy is not None:
            changed = True

        session_start, ss_changed = _hooks.merge_hook(
            hooks.get("SessionStart", legacy),
            self.SESSION_START_COMMAND,
            status_message="Loading agent context",
        )
        hooks["SessionStart"] = session_start

        cwd_changed, cc_changed = _hooks.merge_hook(
            hooks.get("CwdChanged"),
            self.CWD_CHANGED_COMMAND,
            status_message="Checking for AGENTS.md",
        )
        hooks["CwdChanged"] = cwd_changed

        if not (changed or ss_changed or cc_changed):
            if logger:
                logger.info("hooks already wired: %s", settings_path)
            return

        settings["hooks"] = hooks
        if dry_run:
            if logger:
                logger.info("would wire SessionStart + CwdChanged hooks: %s", settings_path)
            return
        _hooks.write_settings(settings_path, settings)
        if logger:
            logger.info("wired SessionStart + CwdChanged hooks: %s", settings_path)

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

    # --- hooks ---------------------------------------------------------
    #
    # Codex ships a hooks framework (learn.chatgpt.com/docs/hooks) whose JSON is
    # **structurally identical** to Claude's -- `hooks.<Event>` is a list of
    # matcher-objects each holding its own `hooks` list of
    # {type, command, statusMessage} -- so `_hooks` merges it unchanged.
    #
    # Context half only. Codex's SessionStart adds "plain text on stdout ... as
    # extra developer context", which is what we need. There is deliberately NO env
    # half: Codex has no CLAUDE_ENV_FILE equivalent -- its hooks *receive* plugin
    # vars (PLUGIN_ROOT/PLUGIN_DATA) but nothing persists exports back into the
    # session. Writing one would be inventing a mechanism.
    #
    # Target `hooks.json`, not `config.toml`: Codex reads either, but a dedicated
    # file means we never rewrite (and risk mangling) the user's main TOML config.
    # Codex warns at startup if one layer has both, so a user with inline [hooks]
    # should pass --no-hooks.
    SESSION_START_COMMAND = "dotagents context"

    # Codex has NO per-session env mechanism: no CLAUDE_ENV_FILE equivalent, no
    # `.env` loading anywhere (confirmed across its full docs set), and no hook that
    # can run before config load -- hooks are *defined in* the config layers, and
    # Codex's earliest event is SessionStart (it has no `Setup` event). So the only
    # way to give Codex our env is `shell_environment_policy.set` in config.toml:
    # "explicit environment overrides injected into every subprocess".
    #
    # That is STATIC -- read once at startup, not recomputed per session. The values
    # therefore go stale when an overlay's env changes, and `dotagents init` is the
    # refresh. `set` MERGES on top of whatever `inherit` admits rather than replacing
    # it, so writing only our AGENTS_* keys leaves the user's environment alone.
    #
    # This is the user's main config (provider/auth settings live here), so the write
    # is a marker-delimited managed block appended to the end -- never a rewrite. It
    # must APPEND: a TOML `[table]` header captures every following key line, so
    # prepending our table would silently swallow the user's top-level keys into it.
    ENV_BLOCK_BEGIN = "# dotagents:begin"
    ENV_BLOCK_END = "# dotagents:end"

    # PATH is excluded: a ~2KB machine-specific absolute list that leaks local
    # layout into a config file, is wrong on any other machine, and -- because
    # `set` overrides per subprocess -- would REPLACE the inherited PATH of
    # everything Codex spawns.
    #
    # Identity vars (AGENT/AGENTS_HARNESS/AGENTS_VENDOR) are deliberately KEPT.
    # They are computed for *this adapter* (`get_environment(explicit=<name>)`),
    # not for whichever harness ran `init`, so Codex's config correctly says
    # AGENT=codex even when initialized from Claude. That is the whole point of a
    # per-agent file.
    ENV_BLOCK_SKIP = frozenset({"PATH"})

    def _config_root(self, config_root: "Optional[Path]" = None) -> Path:
        import os

        if config_root:
            return Path(config_root)
        # CODEX_HOME is Codex's own documented state-dir override.
        return Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))

    @staticmethod
    def _toml_escape(value: str) -> str:
        """Escape a TOML basic-string value (backslash first, then quote)."""
        return value.replace("\\", "\\\\").replace('"', '\\"')

    def write_env_block(
        self,
        env: "dict[str, str]",
        *,
        dry_run: bool,
        logger,
        config_root: "Optional[Path]" = None,
    ) -> None:
        """Write `env` into a managed `[shell_environment_policy]` block in
        `config.toml`, so Codex injects those vars into every subprocess."""
        from dotagents._merge import merge_block

        env = {k: v for k, v in env.items() if k not in self.ENV_BLOCK_SKIP}
        if not env:
            if logger:
                logger.info("no env vars to write for codex")
            return

        root = self._config_root(config_root)
        lines = [
            self.ENV_BLOCK_BEGIN,
            "# Managed by dotagents -- edits inside this block are overwritten by",
            "# `dotagents init`. Values are a snapshot: re-run init after changing",
            "# your env layers. Add your own settings OUTSIDE the markers.",
            "[shell_environment_policy]",
            "set = {%s}"
            % ", ".join(
                '%s = "%s"' % (key, self._toml_escape(env[key])) for key in sorted(env)
            ),
            self.ENV_BLOCK_END,
        ]
        block = "\n".join(lines) + "\n"

        config_path = root / "config.toml"
        if not dry_run:
            config_path.parent.mkdir(parents=True, exist_ok=True)
        branch = merge_block(
            config_path,
            block,
            dry_run=dry_run,
            begin_marker=self.ENV_BLOCK_BEGIN,
            end_marker=self.ENV_BLOCK_END,
            append=True,
        )
        if logger:
            verb = "would write" if dry_run else branch
            logger.info("%s: %s (%d vars)", verb, config_path, len(env))

    def wire_hooks(
        self, dest: Path, *, dry_run: bool, logger, config_root: "Optional[Path]" = None
    ) -> None:
        """Merge our SessionStart hook into `<codex-home>/hooks.json`."""
        from dotagents import _hooks

        root = self._config_root(config_root)

        hooks_path = root / "hooks.json"
        data = _hooks.load_settings(hooks_path)
        hooks = data.get("hooks")
        if not isinstance(hooks, dict):
            hooks = {}

        session_start, changed = _hooks.merge_hook(
            hooks.get("SessionStart"),
            self.SESSION_START_COMMAND,
            status_message="Loading agent context",
        )
        hooks["SessionStart"] = session_start

        if not changed:
            if logger:
                logger.info("hooks already wired: %s", hooks_path)
            return

        data["hooks"] = hooks
        if dry_run:
            if logger:
                logger.info("would wire SessionStart hook: %s", hooks_path)
            return
        _hooks.write_settings(hooks_path, data)
        if logger:
            logger.info("wired SessionStart hook: %s", hooks_path)

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
