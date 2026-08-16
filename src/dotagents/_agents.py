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
    #
    # The PATH prefix makes the hook SELF-SUFFICIENT: `init` populates
    # `<scope>/bin/`, and prefixing it here means the hook finds `dotagents` with no
    # global install and no PATH edit by the user. Without it the hook is one
    # `command not found` away from silently delivering nothing -- which is exactly
    # what happened on this project's own dev box. Project scope comes first so a
    # project's own wrapper wins over the user store's.
    _HOOK_PATH = 'PATH=".agents/bin:$HOME/.agents/bin:$PATH"'
    SESSION_START_COMMAND = (
        'if [ -n "$CLAUDE_ENV_FILE" ]; then '
        '%(path)s dotagents env --diff --format export >> "$CLAUDE_ENV_FILE"; '
        "fi; "
        "%(path)s dotagents context"
    ) % {"path": _HOOK_PATH}
    CWD_CHANGED_COMMAND = "[ -f AGENTS.md ] && cat AGENTS.md || true"

    # Windows without Git Bash: hooks.md's `shell` field docs are explicit --
    # "Defaults to bash, or to powershell on Windows when Git Bash isn't
    # installed." SESSION_START_COMMAND/CWD_CHANGED_COMMAND above are bash
    # syntax with no `shell` set, so on such a machine Claude Code runs them
    # THROUGH POWERSHELL by default -- verified directly: `if [ -n ... ]; then
    # ...; fi` fed to `powershell -Command` is a hard PARSE ERROR
    # ("Missing '(' after 'if'"), not a soft failure. Every session would
    # silently get neither env nor injected context on such a machine. This
    # is real, if less common than having Git Bash installed (per the user:
    # "it happens, just not usually by default").
    #
    # Fix: register a SECOND handler on each event, explicit `shell:
    # "powershell"`, PowerShell-native syntax. Both handlers in a matched
    # group run unconditionally (hooks.md: "every handler in the matched
    # group runs") -- there is no way to select one based on which
    # interpreter is actually present. So exactly one of the two spawns
    # successfully per machine; the other's interpreter is simply absent
    # (Windows without Git Bash has no `sh`/`bash` on PATH) and that spawn
    # fails harmlessly -- SessionStart hook failures do not block the
    # session, only that hook's own effect is lost, which is what would have
    # happened anyway without this fix.
    #
    # The PowerShell variant is CONTEXT-ONLY, not env+context: `$CLAUDE_ENV_FILE`
    # explicitly documents its effect as "subsequent BASH commands" regardless
    # of which shell wrote it, so writing to it from a PowerShell-shelled
    # SessionStart hook would feed nothing -- no Bash call reads a
    # PowerShell-authored write differently, and no PowerShell call reads
    # $CLAUDE_ENV_FILE at all (confirmed empty live, D90). The PowerShell env
    # gap is covered separately by PRETOOLUSE_POWERSHELL_COMMAND below, which
    # works regardless of whether Git Bash is present.
    SESSION_START_COMMAND_POWERSHELL = (
        r'& "$env:USERPROFILE\.agents\bin\dotagents.cmd" context'
    )
    CWD_CHANGED_COMMAND_POWERSHELL = (
        r'if (Test-Path AGENTS.md) { Get-Content AGENTS.md -Raw }'
    )

    # PowerShell tool gap (Windows only): $CLAUDE_ENV_FILE is Bash-tool-only --
    # every hooks.md mention says "subsequent Bash commands", and
    # $env:CLAUDE_ENV_FILE was observed empty inside a live PowerShell tool call.
    # So the SessionStart hook above never reaches PowerShell tool calls at all.
    # Closed independently via a PreToolUse hook (no matcher -- fires for every
    # tool) that, for a PowerShell call specifically, prepends a guarded env-loader
    # to that call's OWN command via `updatedInput`.
    #
    # INLINE `-Command`, deliberately NOT a `.ps1` file: a `.ps1` is subject to
    # PowerShell's script execution policy (RemoteSigned/AllSigned/Restricted),
    # and dotagents has no code-signing certificate to satisfy AllSigned or a
    # locked-down MachinePolicy/UserPolicy (which overrides everything, including
    # Claude Code's own `-ExecutionPolicy Bypass`). An inline `-Command` string is
    # NOT subject to script execution policy at all -- verified directly: it ran
    # successfully even under `Set-ExecutionPolicy -Scope Process Restricted`,
    # which blocks every `.ps1` file. This also removes the earlier `.ps1`
    # design's `&` vs `-File` stdin-forwarding pitfall entirely, since there is
    # no nested spawn -- Claude Code's own hook process (which the shell-form
    # docs confirm receives the real piped stdin) runs this directly.
    #
    # Kept to a single logical statement chain (`;`-joined, matching
    # SESSION_START_COMMAND's own style) so it fits in one settings.json hook
    # command, same as the Bash hooks above.
    #
    # Every literal `\` below MUST use a raw string (or be doubled) up to this
    # point in the source -- a bare `\b` inside a normal Python string literal
    # silently becomes a backspace character (\x08), not the two characters
    # `\`+`b`, corrupting the emitted `\.agents\bin\...` path. Caught by testing
    # a draft of this exact command through a real PowerShell spawn before
    # shipping it, not by inspection -- the corruption is invisible in an editor.
    PRETOOLUSE_POWERSHELL_COMMAND = (
        r'$h = [Console]::In.ReadToEnd() | ConvertFrom-Json; '
        r'if ($h.tool_name -eq "PowerShell" -and -not $env:AGENTS_RUNTIME_SET -and $h.tool_input.command) { '
        r'$p = '
        r"""'if (-not $env:AGENTS_RUNTIME_SET) { $env:AGENTS_RUNTIME_SET = "1"; & "$HOME\.agents\bin\dotagents.cmd" env --format powershell 2>$null | Invoke-Expression }; '; """
        r'$u = $h.tool_input.PSObject.Copy(); '
        r'$u.command = $p + $h.tool_input.command; '
        r'@{hookSpecificOutput=@{hookEventName="PreToolUse";permissionDecision="allow";updatedInput=$u}} | ConvertTo-Json -Depth 10 -Compress '
        r'}'
    )

    def wire_hooks(
        self,
        dest: Path,
        *,
        dry_run: bool,
        logger,
        config_root: "Optional[Path]" = None,
    ) -> None:
        """Link the shared skills dir into `~/.claude` and merge our two hooks.

        Additive and idempotent: unrelated settings keys and foreign hooks survive,
        and a second run writes nothing.
        """
        import os

        from dotagents import _hooks, _skills

        # Scope-aware, like the rest of dotagents: a project-scope `init` must not
        # silently edit the user's GLOBAL settings. `dest` is `<scope>/.agents`, so
        # its parent is the project root in project scope and $HOME in user scope.
        if config_root:
            root = Path(config_root)
        elif Path(dest).expanduser().resolve() == (Path.home() / ".agents").resolve():
            root = Path.home() / ".claude"
        else:
            root = Path(dest).parent / ".claude"

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

        # 2. Hooks. In a project, write `settings.local.json` -- the gitignored
        # personal file. `settings.json` there is checked into source control, and
        # these hooks carry machine-specific paths that must never be committed.
        is_user_scope = root == (Path.home() / ".claude")
        settings_path = root / (
            "settings.json" if is_user_scope else "settings.local.json"
        )
        settings = _hooks.load_settings(settings_path)
        hooks = settings.get("hooks")
        if not isinstance(hooks, dict):
            hooks = {}
        changed = False

        # Legacy lowercase key from the precursor -- fold it in, then drop it.
        legacy = hooks.pop("session_start", None)
        if legacy is not None:
            changed = True

        # Two independent handlers per event: bash-syntax (works with Git Bash on
        # Windows and on every POSIX host) and a PowerShell-native equivalent
        # (works on Windows without Git Bash, where hooks.md's documented default
        # silently routes the bash-syntax command through PowerShell instead --
        # a hard parse error, verified directly). Both fire unconditionally every
        # session (hooks.md: every handler in a matched group runs); the one
        # whose interpreter is absent on this machine fails harmlessly, the other
        # carries the real effect. Chained `merge_hook` calls, each keyed by its
        # own `status_message` so they merge/refresh independently and never
        # collide with each other's identity.
        session_start, ss_changed_1 = _hooks.merge_hook(
            hooks.get("SessionStart", legacy),
            self.SESSION_START_COMMAND,
            status_message="Loading agent context",
        )
        session_start, ss_changed_2 = _hooks.merge_hook(
            session_start,
            self.SESSION_START_COMMAND_POWERSHELL,
            status_message="Loading agent context (PowerShell)",
            shell="powershell",
        )
        hooks["SessionStart"] = session_start
        ss_changed = ss_changed_1 or ss_changed_2

        cwd_changed, cc_changed_1 = _hooks.merge_hook(
            hooks.get("CwdChanged"),
            self.CWD_CHANGED_COMMAND,
            status_message="Checking for AGENTS.md",
        )
        cwd_changed, cc_changed_2 = _hooks.merge_hook(
            cwd_changed,
            self.CWD_CHANGED_COMMAND_POWERSHELL,
            status_message="Checking for AGENTS.md (PowerShell)",
            shell="powershell",
        )
        hooks["CwdChanged"] = cwd_changed
        cc_changed = cc_changed_1 or cc_changed_2

        pt_changed = False
        if os.name == "nt":
            pt_changed = self._wire_powershell_pretooluse(hooks)

        if not (changed or ss_changed or cc_changed or pt_changed):
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
            logger.info(
                "wired SessionStart + CwdChanged%s hooks: %s",
                " + PreToolUse" if pt_changed else "",
                settings_path,
            )

    def _wire_powershell_pretooluse(self, hooks: dict) -> bool:
        """Windows only. Merges a no-matcher `PreToolUse` entry running
        `PRETOOLUSE_POWERSHELL_COMMAND` inline. Returns whether anything changed.

        `shell="powershell"` on the hook entry (per hooks.md's `shell` field)
        means Claude Code spawns PowerShell directly for THIS hook's `command`,
        with no bash/Git-Bash layer in between -- so `PRETOOLUSE_POWERSHELL_COMMAND`
        is passed as-is, with no extra quoting for an outer shell. Nesting a second
        `powershell -Command "..."` inside a bash-tokenized outer command would
        require getting two layers of shell quoting right at once (the command
        contains both `"` and `'`); one spawn avoids that class of bug entirely.

        No file is written to disk for this one -- see the constant's docstring
        for why an inline `-Command` is used instead of a `.ps1` script (execution
        policy). Nothing to make `dry_run`/`agents_home`-aware either, since there
        is no filesystem write left to gate or isolate.
        """
        from dotagents import _hooks

        pretooluse, pt_hook_changed = _hooks.merge_hook(
            hooks.get("PreToolUse"),
            self.PRETOOLUSE_POWERSHELL_COMMAND,
            status_message="Checking PowerShell env",
            shell="powershell",
        )
        hooks["PreToolUse"] = pretooluse

        return pt_hook_changed

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


class AntigravityAgent(Agent):
    name = "antigravity"
    # Documented convention (antigravity.google/docs/rules-workflows): a
    # project-level rules FOLDER, ".agents/rules/" ("Antigravity now defaults
    # to .agents/rules, but still maintains backward support for .agent/rules"),
    # not a single root file the way Claude/Codex/Gemini use. `write_context`
    # below places dotagents' own file inside that folder.
    context_files = [".agents/rules/dotagents.md"]
    # Global rules load from "~/.gemini/GEMINI.md" ("applying across all
    # workspaces") -- shared with GeminiAgent's own file, by the docs'
    # own account, not a separate Antigravity-only global file.
    harness_loads = ["~/.gemini/GEMINI.md", ".agents/rules/dotagents.md"]
    # NO env-var detection marker is documented anywhere (checked hooks,
    # rules-workflows, getting-started, plugins pages) -- unlike Claude
    # (CLAUDECODE=1), Codex (CODEX_HOME), Gemini CLI (GEMINI_CLI=1). Past
    # sessions already invented and had to walk back false markers for other
    # agents (GEMINI_SESSION, CODEX_SESSION) -- not repeating that here.
    # detect_env_vars stays empty, so the base Agent.detect_env
    # (`any(var in environ for var in self.detect_env_vars)`) always returns
    # False: Antigravity is explicit-`--agents antigravity`-only, matching
    # the precedent already set for Codex's env-block (D87/D90 -- writes that
    # touch an agent's own config must be asked for, not auto-triggered).
    detect_env_vars = []
    harness_id = "antigravity"
    vendor = "google"
    # No documented model-source env var found either; left empty rather than
    # guessed (GEMINI_MODEL is GeminiAgent's, a different product's var).
    model_source_vars = []

    def write_base_config(self, dest: Path, src: Path, base_agents_text: str, *, force: bool, dry_run: bool, logger) -> None:
        from dotagents._merge import merge_block, timestamped_backup_root
        backup_root = timestamped_backup_root(dest) if force else None
        branch = merge_block(
            dest / "AGENTS.md",
            base_agents_text,
            force=force, dry_run=dry_run, backup_root=backup_root,
        )
        if logger: logger.info("%s: AGENTS.md (Antigravity)", branch)

    def write_context(self, dest: Path, effective_context: str, *, force: bool, dry_run: bool, logger) -> None:
        target = dest / ".agents" / "rules" / "dotagents.md"
        if not dry_run:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(effective_context, encoding="utf-8")
        if logger: logger.info("wrote context to %s", target)

    # --- hooks ---------------------------------------------------------
    #
    # Antigravity has no SessionStart-equivalent event at all -- confirmed
    # against antigravity.google/docs/hooks: exactly five events exist
    # (PreToolUse, PostToolUse, PreInvocation, PostInvocation, Stop).
    # PreInvocation ("fires before the model is called") is the closest
    # analog, but its own `invocationNum` input field ("the current model
    # invocation... the first invocation is 0") confirms it fires on EVERY
    # turn, not once per session. The hook script gates on invocationNum == 0
    # itself (see preinvocation_antigravity_context.py) so this behaves like
    # a real SessionStart: full context once, a cheap no-op every later turn.
    #
    # Output shape is genuinely different from Claude/Codex's PreToolUse: no
    # `hookSpecificOutput` wrapper -- PreInvocation's documented output is a
    # bare `{"injectSteps": [...]}`. `ephemeralMessage` ("a transient system
    # message") is the field actually meant for injected text; `toolCall` and
    # `userMessage` are for different purposes (executing a tool / impersonating
    # the user).
    #
    # CONTEXT ONLY -- no env half exists for Antigravity. Confirmed twice, from
    # two independent sources (the primary docs AND a third-party article that
    # otherwise contained inaccuracies elsewhere): PreToolUse is allow/deny/ask
    # only, no `updatedInput`-equivalent rewrite mechanism, so there is no way
    # to inject env into a `run_command` tool call the way the Claude/Codex
    # PreToolUse hooks do.
    #
    # No `shell`/`commandWindows`-equivalent field is documented for Antigravity
    # hook commands at all (checked directly, absent) -- the emitted command must
    # therefore work via a bare interpreter invocation with no shell assumptions,
    # and there is exactly ONE string to get right for every platform.
    #
    # It emits `python "<path>"`. That is NOT what Codex does, despite the
    # similar shape: Codex emits `python3` and overrides it to `python` through
    # its `commandWindows` field, precisely because `python3` is the POSIX name
    # and `python3` on Windows hits the Store app-execution-alias stub. With no
    # second field available here, one name has to cover both, and `python` is
    # the one that works on Windows -- but a POSIX box that ships only `python3`
    # (still common) gets a hook that cannot start. Known gap, no fix available
    # inside the hook entry itself; see `.agents/plans/` for the follow-up.
    PRETOOLUSE_HOOK_SCRIPT = "preinvocation_antigravity_context.py"

    def wire_hooks(
        self, dest: Path, *, dry_run: bool, logger, config_root: "Optional[Path]" = None
    ) -> None:
        """Deploys the context-injection script and wires it as a
        `PreInvocation` handler in `<config_root|~/.gemini/config>/hooks.json`.

        Global scope only (`~/.gemini/config/hooks.json`), like the plugin
        directory it mirrors (`~/.gemini/config/plugins/`) -- Antigravity's
        docs describe this as making a plugin/hook "active across all
        workspaces", and PreInvocation carries no per-project matcher to
        scope it more narrowly (confirmed: PreInvocation's matcher is
        documented as ignored).
        """
        import shutil

        from dotagents import _hooks
        from dotagents.cli._common import BASE_ROOT

        root = Path(config_root) if config_root else (Path.home() / ".gemini" / "config")

        src_script = Path(BASE_ROOT) / "dotagents" / "hooks" / self.PRETOOLUSE_HOOK_SCRIPT
        if not src_script.is_file():
            if logger:
                logger.warning("Antigravity hook script missing from package: %s", src_script)
            return

        dest_dir = root / "hooks"
        dest_script = dest_dir / self.PRETOOLUSE_HOOK_SCRIPT
        script_changed = not dest_script.is_file() or (
            dest_script.read_bytes() != src_script.read_bytes()
        )
        if script_changed and not dry_run:
            dest_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(src_script), str(dest_script))

        hooks_path = root / "hooks.json"
        data = _hooks.load_settings(hooks_path)
        # dotagents' own top-level key, matching the docs' example shape
        # (`{"reminder": {"PreInvocation": [...]}}` -- named entries, not a
        # bare top-level "hooks" object the way Claude/Codex's schema is).
        entry = data.get("dotagents")
        if not isinstance(entry, dict):
            entry = {}

        script_path = dest_script.resolve()
        command = 'python "%s"' % script_path.as_posix()
        preinvocation, pi_changed = _hooks.merge_hook(
            entry.get("PreInvocation"),
            command,
            status_message="Loading agent context",
        )
        entry["PreInvocation"] = preinvocation
        data["dotagents"] = entry

        if not (script_changed or pi_changed):
            if logger:
                logger.info("hooks already wired: %s", hooks_path)
            return

        if dry_run:
            if logger:
                logger.info("would wire PreInvocation hook: %s", hooks_path)
            return
        _hooks.write_settings(hooks_path, data)
        if logger:
            logger.info("wired PreInvocation hook: %s", hooks_path)


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
    # Same PATH prefix as Claude's hook, for the same reason: `<scope>/bin/` holds
    # the wrapper `init` wrote, so the hook resolves `dotagents` with no global
    # install and no PATH edit by the user.
    SESSION_START_COMMAND = (
        'PATH=".agents/bin:$HOME/.agents/bin:$PATH" dotagents context'
    )

    # PreToolUse gives Codex the LIVE env half SessionStart cannot: hooks.md
    # confirms PreToolUse supports `updatedInput.command` (learn.chatgpt.com/
    # docs/hooks, "To rewrite a supported tool call without blocking"), the same
    # mechanism and JSON shape as Claude Code's. `matcher: "Bash"` targets
    # Codex's one shell-execution tool -- no separate PowerShell/cmd tool the
    # way Claude has, so (unlike the Claude hook) no runtime tool_name check is
    # needed inside the script itself.
    #
    # Shipped as a FILE (`~/.codex/hooks/pretooluse_codex_env.py`, `python3
    # <path>`), not inlined: hooks.md shows every example hook as a file
    # (`python3 ~/.codex/hooks/*.py`), and a Python script carries none of
    # PowerShell's execution-policy/signing concern -- that constraint was
    # PowerShell-specific, not general to file-based hooks.
    PRETOOLUSE_HOOK_SCRIPT = "pretooluse_codex_env.py"

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
        """Merge SessionStart + PreToolUse into `<codex-home>/hooks.json`, and
        deploy the PreToolUse env-loader script alongside it."""
        from dotagents import _hooks

        root = self._config_root(config_root)

        script_changed = self._deploy_pretooluse_script(root, dry_run=dry_run, logger=logger)

        hooks_path = root / "hooks.json"
        data = _hooks.load_settings(hooks_path)
        hooks = data.get("hooks")
        if not isinstance(hooks, dict):
            hooks = {}

        session_start, ss_changed = _hooks.merge_hook(
            hooks.get("SessionStart"),
            self.SESSION_START_COMMAND,
            status_message="Loading agent context",
        )
        hooks["SessionStart"] = session_start

        # Absolute path, quoted for a possible space (e.g. under a Windows
        # %USERPROFILE% containing one). Not `$HOME`-relative like the Bash
        # SessionStart commands: those are portable shell snippets meant to
        # read the same on any machine, but this hooks.json is itself
        # machine-local config generated fresh by THIS `wire_hooks` call, on
        # the machine it will run on -- there is no portability requirement.
        #
        # `commandWindows` uses `python`, not `python3`: on Windows `python3`
        # is commonly a Microsoft Store app-execution-alias stub that can
        # silently no-op (this session's own dev box has exactly that --
        # `python3` resolves to the Store alias, `python` to the real
        # interpreter). Codex's docs only ever show `python3`, presumably
        # written for POSIX hosts; `commandWindows` is the documented
        # Windows-only override, same mechanism as Claude's dual-shell hooks.
        script_path = (root / "hooks" / self.PRETOOLUSE_HOOK_SCRIPT).resolve()
        pretooluse_command = 'python3 "%s"' % script_path.as_posix()
        pretooluse_command_windows = 'python "%s"' % str(script_path)
        pretooluse, pt_changed = _hooks.merge_hook(
            hooks.get("PreToolUse"),
            pretooluse_command,
            matcher="Bash",
            status_message="Loading agent env",
            command_windows=pretooluse_command_windows,
        )
        hooks["PreToolUse"] = pretooluse

        if not (ss_changed or pt_changed or script_changed):
            if logger:
                logger.info("hooks already wired: %s", hooks_path)
            return

        data["hooks"] = hooks
        if dry_run:
            if logger:
                logger.info("would wire SessionStart + PreToolUse hooks: %s", hooks_path)
            return
        _hooks.write_settings(hooks_path, data)
        if logger:
            logger.info("wired SessionStart + PreToolUse hooks: %s", hooks_path)

    def _deploy_pretooluse_script(self, root: Path, *, dry_run: bool, logger) -> bool:
        """Copies `pretooluse_codex_env.py` to `<codex-home>/hooks/`
        (create-or-refresh -- this script has no user-editable region, same
        contract as the base overlay's other bundled files). Returns whether
        it changed."""
        import shutil

        from dotagents.cli._common import BASE_ROOT

        src_script = Path(BASE_ROOT) / "dotagents" / "hooks" / self.PRETOOLUSE_HOOK_SCRIPT
        if not src_script.is_file():
            if logger:
                logger.warning("Codex PreToolUse script missing from package: %s", src_script)
            return False

        dest_dir = root / "hooks"
        dest_script = dest_dir / self.PRETOOLUSE_HOOK_SCRIPT
        changed = not dest_script.is_file() or (
            dest_script.read_bytes() != src_script.read_bytes()
        )
        if changed and not dry_run:
            dest_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(src_script), str(dest_script))
        return changed

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
    "antigravity": AntigravityAgent,
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
    and, when derivable, ``AGENTS_MODEL`` (from the adapter's vendor model var).

    Never emits a var it cannot source (no empty ``AGENTS_MODEL=``). Does NOT
    clobber a value already set in ``environ`` -- an explicit user/harness value
    wins. The deliberate curated mapping replaces the precursor's blanket
    ``CLAUDE_*``->``AGENTS_*`` rewrite (no ``AGENTS_CODE_SESSION_ID`` junk).

    ``AGENTS_AGENT`` (a named persona) is NOT emitted and never was: the line
    that claimed to "surface an already-selected persona" read the value from
    the same key its own not-already-set guard required to be absent, so it was
    unreachable in both directions. A persona would have to be DERIVED from
    something (e.g. a ``<store>/<agent>.md``), which was never ported -- until
    it is, nothing may branch on this var.
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

    return identity
