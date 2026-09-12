"""Agent registry: base Agent type + per-agent adapters."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional

from dotagents._fs import write_text_lf


def _is_user_store(dest: "str | os.PathLike[str]") -> bool:
    """True if ``dest`` is the user store as ``resolve_user_store`` defines it
    (``$AGENTS_HOME`` → ``~/.agents``), not the literal home path: a custom
    store must still get user-scope treatment."""
    from dotagents._scope import resolve_user_store

    try:
        return Path(dest).expanduser().resolve() == resolve_user_store().resolve()
    except OSError:
        return False


#: A Claude Code `@path` include line: the path is the rest of the line.
_INCLUDE_LINE_RE = re.compile(r"^\s*@(\S+)\s*$")


def _claude_includes(entry_file: Path, seen: "set[Path]") -> None:
    """Add ``entry_file`` and every file it ``@``-includes (recursively,
    relative to the including file, ``~`` expanded) to ``seen``."""
    try:
        resolved = entry_file.expanduser().resolve()
    except OSError:
        return
    if resolved in seen or not resolved.is_file():
        return
    seen.add(resolved)
    try:
        lines = resolved.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        m = _INCLUDE_LINE_RE.match(line)
        if not m:
            continue
        ref = Path(m.group(1)).expanduser()
        if not ref.is_absolute():
            ref = resolved.parent / ref
        _claude_includes(ref, seen)


class Agent:
    """Base class for dotagents adapters.

    Identity: each adapter declares its ``harness_id`` (the stable ecosystem
    name, e.g. ``claude-code`` -- NOT the short registry ``name``), ``vendor``
    (provider family), and ``model_source_vars`` (the vendor-native env vars to
    read a running model id from, in precedence order). These feed
    ``stamp_identity`` which emits the standardized ``AGENTS_*``/``AGENT`` vars.
    """

    name: str = ""
    context_files: list[str] = []
    harness_loads: list[str] = []
    detect_env_vars: list[str] = []

    # --- identity mapping ----------------------------------------------
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
        """Write the base configuration files for this agent (used by init)."""
        pass

    #: The harness's own instruction file, relative to the PROJECT ROOT, that
    #: `write_context` merges the assembled context into (as a managed
    #: `dotagents:context` block, never a raw overwrite). Empty = no such file.
    context_target: str = ""

    def write_context(self, project_root: Path, effective_context: str, *, force: bool, dry_run: bool, logger) -> None:
        """Merge the assembled context into this harness's own instruction file
        under ``project_root`` (:attr:`context_target`), as a managed block.

        ``project_root`` is the project, NOT the store: a store's ``AGENTS.md``
        is a context SOURCE and writing there would re-inline the previous
        run's output. Prefer the SessionStart hook where the harness has one;
        this is the static alternative for a harness without hooks."""
        if not self.context_target:
            if logger:
                logger.info("%s: no native context file to write", self.name)
            return
        from dotagents._merge import merge_context_block

        target = Path(project_root) / self.context_target
        branch = merge_context_block(target, effective_context, dry_run=dry_run)
        if logger:
            logger.info("%s: %s", "would write" if dry_run else branch, target)

    def loaded_paths(self, project_root: Path) -> "list[Path]":
        """Resolved paths this harness loads by itself, so ``context`` never
        double-sends them. The static :attr:`harness_loads` entries: ``~/`` and
        ``/`` forms are absolute, anything else is relative to ``project_root``.
        A harness with an include mechanism overrides this to report what its
        entry files actually include on this machine."""
        out: "list[Path]" = []
        for hl in self.harness_loads:
            try:
                if hl.startswith("~/") or hl.startswith("/"):
                    out.append(Path(hl).expanduser().resolve())
                else:
                    out.append((Path(project_root) / hl).resolve())
            except OSError:
                pass
        return out

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

    #: The harness's command-line program, for ``dotagents launch``: a bare
    #: name resolved on the PATH the launch exports. Empty = no CLI dotagents
    #: knows how to start (an IDE-only harness); ``launch --command`` overrides.
    launch_command: str = ""

    def launch_context_args(self, context_file: Path) -> "Optional[list[str]]":
        """Argv that hands ``context_file`` (the assembled context, markdown) to
        a fresh session as text APPENDED to the harness's own system prompt.

        ``None`` = the harness has no append mechanism, and ``launch`` falls
        back to :meth:`write_context` (the managed block in
        :attr:`context_target`, which the harness loads by itself). Only a
        documented append flag earns an override: Codex's
        ``model_instructions_file`` and Gemini's ``GEMINI_SYSTEM_MD`` REPLACE
        the built-in prompt, which is not the same thing and is documented to
        degrade the harness."""
        return None


class ClaudeAgent(Agent):
    name = "claude"
    context_files = ["CLAUDE.md"]
    # What Claude Code loads by itself: the project-root AGENTS.md, plus whatever
    # its entry files `@`-include -- resolved live by `loaded_paths`, never
    # assumed statically (the store's AGENTS.md is loaded only if an include
    # actually points at it).
    harness_loads = ["AGENTS.md"]
    #: Claude Code's entry files, relative to the project root (`~/`-prefixed =
    #: the user-level one). `@path` lines in any of them are includes.
    ENTRY_FILES = ("~/.claude/CLAUDE.md", "CLAUDE.md", "CLAUDE.local.md", ".claude/CLAUDE.md")
    context_target = ".claude/CLAUDE.md"
    # Documented markers (code.claude.com/docs/en/env-vars): CLAUDECODE=1 plus the
    # CLAUDE_CODE_* family (CLAUDE_CODE_ENTRYPOINT, ...).
    detect_env_vars = ["CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT"]
    harness_id = "claude-code"
    vendor = "anthropic"
    model_source_vars = ["ANTHROPIC_MODEL"]
    launch_command = "claude"

    def launch_context_args(self, context_file: Path) -> "Optional[list[str]]":
        # The file form, not `--append-system-prompt <text>`: the text is
        # multi-line markdown, and on Windows `claude` is an npm `.cmd` shim
        # that cmd.exe re-parses -- a newline inside an argument ends the
        # command there. Both flags work in interactive and -p mode
        # (code.claude.com/docs/en/cli-reference, system prompt flags).
        return ["--append-system-prompt-file", str(context_file)]

    def loaded_paths(self, project_root: Path) -> "list[Path]":
        seen: "set[Path]" = set()
        for entry in self.ENTRY_FILES:
            if entry.startswith("~/"):
                _claude_includes(Path(entry), seen)
            else:
                _claude_includes(Path(project_root) / entry, seen)
        return super().loaded_paths(project_root) + sorted(seen)

    def _config_root(self, dest: Path) -> Path:
        """`~/.claude` for the user store, `<project>/.claude` otherwise
        (`dest` is `<scope>/.agents`, so its parent is the project root)."""
        if _is_user_store(dest):
            return Path.home() / ".claude"
        return Path(dest).expanduser().resolve().parent / ".claude"

    def _include_line(self, dest: Path, entry_file: Path) -> str:
        """The `@path` line that makes `entry_file` load `<dest>/AGENTS.md`:
        relative when the store sits beside the config dir (`@../.agents/AGENTS.md`
        -- the store stays relocatable), absolute otherwise."""
        target = Path(dest).expanduser().resolve() / "AGENTS.md"
        try:
            rel = Path(os.path.relpath(target, entry_file.parent))
        except ValueError:  # different drives
            rel = None
        if rel is not None and rel.parts.count("..") <= 1:
            return "@" + rel.as_posix()
        return "@" + target.as_posix()

    def write_base_config(self, dest: Path, src: Path, base_agents_text: str, *, force: bool, dry_run: bool, logger) -> None:
        from dotagents._merge import merge_block, merge_include_line, timestamped_backup_root

        backup_root = timestamped_backup_root(dest) if force else None

        branch = merge_block(
            dest / "AGENTS.md",
            base_agents_text,
            force=force, dry_run=dry_run, backup_root=backup_root,
        )
        if logger: logger.info("%s: AGENTS.md", branch)

        # The last mile: Claude Code reads `~/.claude/CLAUDE.md` (user scope) or
        # `<project>/.claude/CLAUDE.md` (project scope), never a `<store>/CLAUDE.md`.
        # Without this include the store's AGENTS.md reaches no session at all.
        # A managed block, appended, and skipped when the include line is already
        # there by hand.
        entry = self._config_root(dest) / "CLAUDE.md"
        branch = merge_include_line(
            entry, self._include_line(dest, entry), dry_run=dry_run,
        )
        if logger: logger.info("%s: %s (include)", branch, entry)

    # --- hooks ---------------------------------------------------------
    #
    # SessionStart does two things, in one `type: command` hook (SessionStart
    # supports only command/mcp_tool hooks, and env must run before context):
    #
    # 1. `dotagents env --diff` output is appended to $CLAUDE_ENV_FILE
    #    (code.claude.com/docs/en/env-vars + hooks.md "Persist environment
    #    variables"): Claude sources that file before each Bash command. It is
    #    set only inside SessionStart/Setup/CwdChanged/FileChanged hook
    #    processes, never in the session's own environment.
    #      * APPEND (`>>`), never truncate -- other hooks write to the same file.
    #      * Guard on non-empty (`[ -n "$CLAUDE_ENV_FILE" ]`) -- when unset,
    #        `>> ""` would create a file literally named "".
    #
    # 2. `dotagents context`: Claude injects a SessionStart hook's stdout into
    #    the session context.
    #
    # The PATH prefix makes the hook self-sufficient: `init` populates
    # `<scope>/bin/`, so the hook finds `dotagents` with no global install and
    # no PATH edit. Project scope comes first so a project's own wrapper wins.
    # The store is `$AGENTS_HOME` when set, `~/.agents` otherwise -- the chain
    # `resolve_user_store` walks.
    _HOOK_PATH = 'PATH=".agents/bin:${AGENTS_HOME:-$HOME/.agents}/bin:$PATH"'
    SESSION_START_COMMAND = (
        'if [ -n "$CLAUDE_ENV_FILE" ]; then '
        '%(path)s dotagents env --diff --format export >> "$CLAUDE_ENV_FILE"; '
        "fi; "
        "%(path)s dotagents context"
    ) % {"path": _HOOK_PATH}
    # On `cd` into another project: re-pin AGENTS_PROJECT_ROOT for the rest of
    # the session (the SessionStart pin is only-if-unset), then show that
    # project's root AGENTS.md. `pwd -W` is Git Bash's Windows-native form
    # (`C:/...`) -- a `/c/...` MSYS path would be misread by the Windows Python
    # that runs `dotagents`; elsewhere `pwd -W` fails and plain `pwd` is used.
    CWD_CHANGED_COMMAND = (
        'if [ -n "$CLAUDE_ENV_FILE" ] && [ -d .agents ]; then '
        "printf \"export AGENTS_PROJECT_ROOT='%s'\\n\" \"$(pwd -W 2>/dev/null || pwd)\" "
        '>> "$CLAUDE_ENV_FILE"; fi; '
        "[ -f AGENTS.md ] && cat AGENTS.md || true"
    )

    # Windows without Git Bash: hooks.md's `shell` field "defaults to bash, or
    # to powershell on Windows when Git Bash isn't installed", so the bash
    # commands above would be fed to PowerShell and fail to parse. Hence a
    # SECOND handler per event with explicit `shell: "powershell"` and
    # PowerShell-native syntax. Every handler in a matched group runs, so the
    # PowerShell variant SELECTS ITSELF: it runs only when `bash` is not on
    # PATH, or a box with both would inject the context twice.
    #
    # The PowerShell variant is CONTEXT-ONLY: `$CLAUDE_ENV_FILE` only affects
    # subsequent Bash commands, whichever shell wrote it, and PowerShell tool
    # calls never read it. The PowerShell env gap is covered separately by
    # PRETOOLUSE_POWERSHELL_COMMAND below, which works with or without Git
    # Bash present.
    #
    # The store is `$env:AGENTS_HOME` when set, `$HOME\.agents` otherwise, and
    # the project's own `.agents\bin` wins when present -- the same resolution
    # as the bash variant's PATH prefix.
    _PS_NO_BASH = r'if (-not (Get-Command bash -ErrorAction SilentlyContinue)) { '
    _PS_STORE = r'$(if ($env:AGENTS_HOME) { $env:AGENTS_HOME } else { "$HOME\.agents" })'
    _PS_DOTAGENTS = (
        r'$d = if (Test-Path ".agents\bin\dotagents.cmd") { ".agents\bin\dotagents.cmd" } '
        r'else { "' + _PS_STORE + r'\bin\dotagents.cmd" }'
    )
    SESSION_START_COMMAND_POWERSHELL = _PS_NO_BASH + _PS_DOTAGENTS + r'; & $d context }'
    CWD_CHANGED_COMMAND_POWERSHELL = (
        _PS_NO_BASH + r'if (Test-Path AGENTS.md) { Get-Content AGENTS.md -Raw } }'
    )

    # PowerShell tool gap (Windows only): $CLAUDE_ENV_FILE affects Bash tool
    # calls only, so the SessionStart hook never reaches PowerShell tool calls.
    # Closed via a PreToolUse hook (no matcher -- fires for every tool) that,
    # for a PowerShell call specifically, prepends a guarded env-loader to that
    # call's OWN command via `updatedInput`.
    #
    # INLINE `-Command`, deliberately NOT a `.ps1` file: a `.ps1` is subject to
    # PowerShell's script execution policy (AllSigned, or a locked-down
    # MachinePolicy/UserPolicy that overrides Claude Code's own
    # `-ExecutionPolicy Bypass`), and dotagents has no signing certificate. An
    # inline `-Command` string is not subject to the policy, and with no
    # nested spawn the hook process's piped stdin is read directly.
    #
    # A single `;`-joined statement chain so it fits in one settings.json hook
    # command, like the Bash hooks above.
    #
    # `env --diff`, not the full env: each PowerShell tool call is its own
    # process (the AGENTS_RUNTIME_SET guard never survives to the next call),
    # so the loader runs on EVERY call; the change set is all it needs. The
    # store is `$env:AGENTS_HOME` when set.
    #
    # Every literal `\` below MUST be in a raw string (or doubled): a bare `\b`
    # in a normal Python string literal silently becomes a backspace (\x08),
    # corrupting the emitted `\.agents\bin\...` path invisibly.
    PRETOOLUSE_POWERSHELL_COMMAND = (
        r'$h = [Console]::In.ReadToEnd() | ConvertFrom-Json; '
        r'if ($h.tool_name -eq "PowerShell" -and -not $env:AGENTS_RUNTIME_SET -and $h.tool_input.command) { '
        r'$p = '
        r"""'if (-not $env:AGENTS_RUNTIME_SET) { $env:AGENTS_RUNTIME_SET = "1"; $s = if ($env:AGENTS_HOME) { $env:AGENTS_HOME } else { "$HOME\.agents" }; & "$s\bin\dotagents.cmd" env --diff --format powershell 2>$null | Invoke-Expression }; '; """
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
        # its parent is the project root in project scope; the user store is
        # whatever `resolve_user_store` says (`$AGENTS_HOME`), not literally
        # `~/.agents`.
        is_user_scope = _is_user_store(dest)
        if config_root:
            root = Path(config_root)
            is_user_scope = False
        else:
            root = self._config_root(dest)

        # 1. Skills last mile. Publishing into `<scope>/skills/` only helps if the
        #    agent reads that dir; without this link it never does. PER SKILL,
        #    into `<config>/skills/<name>`, so a user's own `~/.claude/skills`
        #    dir is never a conflict. A same-named skill the user placed there
        #    by hand is a conflict and is left alone, with a warning.
        shared_skills = Path(dest) / "skills"
        skill_dirs = (
            sorted(d for d in shared_skills.iterdir() if d.is_dir())
            if shared_skills.is_dir() else []
        )
        if not skill_dirs:
            if logger:
                logger.info("no skills to link (%s has none)", shared_skills)
        elif dry_run:
            if logger:
                logger.info(
                    "would link %d skill(s): %s -> %s", len(skill_dirs), shared_skills,
                    root / "skills",
                )
        else:
            copied = False
            for skill in skill_dirs:
                result = _skills.sync_path(skill, root / "skills" / skill.name, prefer_symlink=True)
                if not result.success:
                    if logger:
                        logger.warning("skill %s not linked: %s", skill.name, result.message)
                    continue
                copied = copied or result.mode == "copy"
                if logger and "already" not in result.message:
                    logger.info("skill %s (%s): %s", skill.name, result.mode, result.message)
            if copied and logger:
                logger.warning(
                    "some skills were COPIED, not symlinked (no symlink support here): "
                    "a copy is a point-in-time snapshot and goes stale when overlay "
                    "skills change -- re-run `dotagents init` to refresh it"
                )

        # 2. Hooks. In a project, write `settings.local.json` -- the gitignored
        # personal file. `settings.json` there is checked into source control, and
        # these hooks carry machine-specific paths that must never be committed.
        settings_path = root / (
            "settings.json" if is_user_scope else "settings.local.json"
        )
        settings = _hooks.load_settings(settings_path)
        hooks = settings.get("hooks")
        if not isinstance(hooks, dict):
            hooks = {}
        changed = False

        # Per event, a bash-syntax handler -- and on WINDOWS a second,
        # PowerShell-native one (`shell: powershell`) that selects itself only
        # when `bash` is absent (see the constants above). On POSIX Claude Code
        # runs a `shell: powershell` entry through bash regardless, so the gate
        # is the OS, never "is pwsh installed", and the PowerShell entries are
        # RETRACTED there so an existing install converges on the next `init`.
        # Each `merge_hook`/`remove_hook` is keyed by its own `status_message`,
        # so the variants never collide with each other's identity.
        windows = self._is_windows()
        events = (
            ("SessionStart", hooks.get("SessionStart"), self.SESSION_START_COMMAND,
             self.SESSION_START_COMMAND_POWERSHELL, "Loading agent context"),
            ("CwdChanged", hooks.get("CwdChanged"), self.CWD_CHANGED_COMMAND,
             self.CWD_CHANGED_COMMAND_POWERSHELL, "Checking for AGENTS.md"),
        )
        for event, existing, bash_command, ps_command, status in events:
            entries, c = _hooks.merge_hook(existing, bash_command, status_message=status)
            changed |= c
            if windows:
                entries, c = _hooks.merge_hook(
                    entries, ps_command, status_message=status + " (PowerShell)", shell="powershell",
                )
            else:
                entries, c = _hooks.remove_hook(entries, status + " (PowerShell)")
            changed |= c
            hooks[event] = entries

        if windows:
            changed |= self._wire_powershell_pretooluse(hooks)
        else:
            pretooluse, c = _hooks.remove_hook(hooks.get("PreToolUse"), self.PRETOOLUSE_STATUS)
            if c:
                changed = True
                if pretooluse:
                    hooks["PreToolUse"] = pretooluse
                else:
                    hooks.pop("PreToolUse", None)

        if not changed:
            if logger:
                logger.info("hooks already wired: %s", settings_path)
            return

        settings["hooks"] = hooks
        wired = "SessionStart + CwdChanged" + (" + PreToolUse" if windows else "")
        if dry_run:
            if logger:
                logger.info("would wire %s hooks: %s", wired, settings_path)
            return
        _hooks.write_settings(settings_path, settings)
        if logger:
            logger.info("wired %s hooks: %s", wired, settings_path)

    @staticmethod
    def _is_windows() -> bool:
        """The platform gate for the PowerShell hook variants, as a seam: tests
        patch THIS (`os.name` cannot be patched -- `pathlib.Path()` dispatches
        on it -- and the real gate must stay `os.name`, not "pwsh present")."""
        return os.name == "nt"

    #: Identity (statusMessage) of the PowerShell PreToolUse env-loader entry.
    PRETOOLUSE_STATUS = "Checking PowerShell env"

    def _wire_powershell_pretooluse(self, hooks: dict) -> bool:
        """Windows only. Merges a no-matcher `PreToolUse` entry running
        `PRETOOLUSE_POWERSHELL_COMMAND` inline. Returns whether anything changed.

        `shell="powershell"` makes Claude Code spawn PowerShell directly for
        this hook's `command`, so the string is passed as-is with no outer-shell
        quoting layer (it contains both `"` and `'`). No file is written, so
        there is nothing to gate on `dry_run`.
        """
        from dotagents import _hooks

        pretooluse, pt_hook_changed = _hooks.merge_hook(
            hooks.get("PreToolUse"),
            self.PRETOOLUSE_POWERSHELL_COMMAND,
            status_message=self.PRETOOLUSE_STATUS,
            shell="powershell",
        )
        hooks["PreToolUse"] = pretooluse

        return pt_hook_changed


class GeminiAgent(Agent):
    name = "gemini"
    context_files = ["GEMINI.md"]
    harness_loads = ["GEMINI.md"]
    context_target = "GEMINI.md"
    # Documented marker: GEMINI_CLI=1, set by Gemini CLI in every child process
    # it spawns (google-gemini.github.io/gemini-cli, run_shell_command docs).
    # GEMINI_API_KEY is a credential, not a marker.
    detect_env_vars = ["GEMINI_CLI"]
    harness_id = "gemini-cli"
    launch_command = "gemini"
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


class AntigravityAgent(Agent):
    name = "antigravity"
    # Documented convention (antigravity.google/docs/rules-workflows): a
    # project-level rules FOLDER, ".agents/rules/", not a single root file.
    # `write_context` places dotagents' own file inside that folder.
    context_files = [".agents/rules/dotagents.md"]
    # Global rules load from "~/.gemini/GEMINI.md" -- shared with GeminiAgent's
    # own file, per the docs, not a separate Antigravity-only global file.
    harness_loads = ["~/.gemini/GEMINI.md", ".agents/rules/dotagents.md"]
    # The rules-folder file is OURS (nothing else writes it), so the whole file
    # is the managed block -- `merge_context_block` creates/refreshes it.
    context_target = ".agents/rules/dotagents.md"
    # No env-var detection marker is documented (hooks, rules-workflows,
    # getting-started, plugins pages), so none is guessed: `detect_env` always
    # returns False and Antigravity is explicit-`--agents antigravity`-only
    # (writes touching an agent's own config must be asked for, D87/D90).
    detect_env_vars = []
    harness_id = "antigravity"
    vendor = "google"
    # No documented model-source env var either; left empty rather than
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

    # --- hooks ---------------------------------------------------------
    #
    # Antigravity has no SessionStart-equivalent event (antigravity.google/
    # docs/hooks: PreToolUse, PostToolUse, PreInvocation, PostInvocation, Stop).
    # PreInvocation is the closest analog but fires on EVERY model turn; the
    # hook script gates on `invocationNum == 0` itself so this behaves like a
    # SessionStart: full context once, a cheap no-op every later turn.
    #
    # PreInvocation's documented output is a bare `{"injectSteps": [...]}` (no
    # `hookSpecificOutput` wrapper); `ephemeralMessage` is the field for
    # injected text.
    #
    # CONTEXT ONLY: Antigravity's PreToolUse is allow/deny/ask with no
    # `updatedInput`-equivalent, so there is no way to inject env into a
    # `run_command` call the way the Claude/Codex PreToolUse hooks do.
    #
    # No `shell`/`commandWindows`-equivalent field is documented, so ONE
    # command string must work everywhere. It emits `python "<path>"`: that is
    # the name that works on Windows (`python3` there is commonly the Store
    # app-execution-alias stub), so a POSIX box that ships only `python3` gets
    # a hook that cannot start. Known gap, no fix available inside the hook
    # entry itself.
    PREINVOCATION_HOOK_SCRIPT = "preinvocation_antigravity_context.py"

    def wire_hooks(
        self, dest: Path, *, dry_run: bool, logger, config_root: "Optional[Path]" = None
    ) -> None:
        """Deploys the context-injection script and wires it as a
        `PreInvocation` handler in `<config_root|~/.gemini/config>/hooks.json`.

        Global scope only (`~/.gemini/config/hooks.json`), like the plugin
        directory it mirrors (`~/.gemini/config/plugins/`): PreInvocation's
        matcher is documented as ignored, so there is no per-project scoping.
        """
        import shutil

        from dotagents import _hooks
        from dotagents.cli._common import BASE_ROOT

        root = Path(config_root) if config_root else (Path.home() / ".gemini" / "config")

        src_script = Path(BASE_ROOT) / "dotagents" / "hooks" / self.PREINVOCATION_HOOK_SCRIPT
        if not src_script.is_file():
            if logger:
                logger.warning("Antigravity hook script missing from package: %s", src_script)
            return

        dest_dir = root / "hooks"
        dest_script = dest_dir / self.PREINVOCATION_HOOK_SCRIPT
        script_changed = not dest_script.is_file() or (
            dest_script.read_bytes() != src_script.read_bytes()
        )
        if script_changed and not dry_run:
            dest_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(src_script), str(dest_script))

        hooks_path = root / "hooks.json"
        data = _hooks.load_settings(hooks_path)
        # dotagents' own top-level key, matching the docs' shape of named
        # entries (`{"<name>": {"PreInvocation": [...]}}`), not a bare
        # top-level "hooks" object like Claude/Codex's schema.
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
    context_target = "AGENTS.md"
    # Codex ships no dedicated runtime marker (openai/codex). Detect via the
    # sandbox vars it sets for child processes (CODEX_SANDBOX, any
    # CODEX_SANDBOX_* -- matched by prefix in detect_env below). CODEX_HOME is
    # NOT a marker: it is the user's state-dir override, typically exported from
    # a shell profile, so it is present in every harness's sessions on that box.
    # `_config_root` still honours it as the config location.
    detect_env_vars = ["CODEX_SANDBOX"]
    harness_id = "codex"
    launch_command = "codex"
    vendor = "openai"
    # Codex has no dedicated model var, so read the OpenAI one.
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
    # Codex's hooks JSON (learn.chatgpt.com/docs/hooks) is structurally
    # identical to Claude's -- `hooks.<Event>` is a list of matcher-objects
    # each holding its own `hooks` list -- so `_hooks` merges it unchanged.
    #
    # Context half only: Codex's SessionStart adds "plain text on stdout ... as
    # extra developer context". No env half: Codex has no CLAUDE_ENV_FILE
    # equivalent -- nothing persists exports back into the session.
    #
    # Target `hooks.json`, not `config.toml`: Codex reads either, and a
    # dedicated file means never rewriting the user's main TOML config. Codex
    # warns at startup if one layer has both, so a user with inline [hooks]
    # should pass --no-hooks.
    # Same PATH prefix as Claude's hook, for the same reason: `<scope>/bin/`
    # holds the wrapper `init` wrote.
    SESSION_START_COMMAND = (
        'PATH=".agents/bin:${AGENTS_HOME:-$HOME/.agents}/bin:$PATH" dotagents context'
    )

    # PreToolUse gives Codex the LIVE env half SessionStart cannot: its
    # PreToolUse supports `updatedInput.command` with the same JSON shape as
    # Claude Code's. `matcher: "Bash"` targets Codex's one shell-execution
    # tool, so no runtime tool_name check is needed inside the script.
    #
    # Shipped as a FILE (`<codex-home>/hooks/pretooluse_codex_env.py`), the
    # form Codex's docs show for every hook; a Python script has no
    # execution-policy concern.
    PRETOOLUSE_HOOK_SCRIPT = "pretooluse_codex_env.py"

    # Codex has no per-session env mechanism (no CLAUDE_ENV_FILE equivalent, no
    # `.env` loading, no hook before config load), so the only way to give it
    # our env is `shell_environment_policy.set` in config.toml: "explicit
    # environment overrides injected into every subprocess".
    #
    # That is STATIC -- read once at startup. The values go stale when an
    # overlay's env changes, and `dotagents init` is the refresh. `set` MERGES
    # on top of whatever `inherit` admits, so writing only our keys leaves the
    # user's environment alone.
    #
    # This is the user's main config, so the write is a marker-delimited
    # managed block APPENDED to the end: a TOML `[table]` header captures every
    # following key line, so prepending would swallow the user's top-level keys.
    ENV_BLOCK_BEGIN = "# dotagents:begin"
    ENV_BLOCK_END = "# dotagents:end"

    # PATH is excluded: a machine-specific absolute list that is wrong on any
    # other machine and -- because `set` overrides per subprocess -- would
    # REPLACE the inherited PATH of everything Codex spawns.
    #
    # Identity vars (AGENT/AGENTS_HARNESS/AGENTS_VENDOR) are KEPT: they are
    # computed for *this adapter* (`get_environment(explicit=<name>)`), so
    # Codex's config says AGENT=codex even when initialized from Claude.
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

        # Absolute path, quoted for a possible space: this hooks.json is
        # machine-local config written on the machine it runs on, so unlike
        # the Bash SessionStart snippets it need not be portable.
        #
        # `commandWindows` uses `python`, not `python3`: on Windows `python3`
        # is commonly a Microsoft Store app-execution-alias stub that silently
        # no-ops. `commandWindows` is Codex's documented Windows-only override.
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
        (create-or-refresh: the script has no user-editable region). Returns
        whether it changed."""
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


class CursorAgent(Agent):
    name = "cursor"
    context_files = [".cursorrules", ".cursor/rules/"]
    harness_loads = [".cursorrules", ".cursor/rules/"]
    context_target = ".cursorrules"
    # Documented marker: CURSOR_AGENT=1 (cursor.com/docs/cli). It is not always
    # propagated to spawned bash (forum.cursor.com/t/.../132427), so config-file
    # detect() remains the fallback.
    detect_env_vars = ["CURSOR_AGENT"]
    harness_id = "cursor"
    launch_command = "cursor-agent"
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


class CopilotAgent(Agent):
    name = "copilot"
    context_files = [".github/copilot-instructions.md"]
    harness_loads = [".github/copilot-instructions.md"]
    context_target = ".github/copilot-instructions.md"
    # Copilot ships no runtime marker var (request open: microsoft/vscode#311734).
    # COPILOT_MODEL / COPILOT_HOME are config, not "am I running" markers, so
    # detection falls back to config-file detect().
    detect_env_vars = []
    harness_id = "copilot"
    launch_command = "copilot"
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


class PiAgent(Agent):
    """pi (pi.dev; npm ``@earendil-works/pi-coding-agent``; the ``pi`` command).

    Documented conventions (packages/coding-agent/README.md and
    docs/environment-variables.md in badlogic/pi-mono):

    * Context files: ``AGENTS.md`` / ``CLAUDE.md`` (``AGENTS.override.md``
      replaces both in its directory), read from the config dir
      (``~/.pi/agent``, ``$PI_CODING_AGENT_DIR`` overrides) first, then every
      parent of the cwd, then the cwd. No include syntax.
    * System prompt: ``--system-prompt <text>`` replaces, ``--append-system-prompt
      <text>`` appends; the file forms are ``SYSTEM.md`` (replaces) and
      ``APPEND_SYSTEM.md`` (appends), in ``<project>/.pi/`` or the config dir.
    * Markers in every process its tools run: ``PI_CODING_AGENT=true``,
      ``AI_AGENT=pi``; ``PI_MODEL`` / ``PI_PROVIDER`` name the selected model.
      pi is multi-provider, so it has no vendor of its own.
    """
    name = "pi"
    # The project-level files only pi writes or reads -- AGENTS.md alone would
    # make every repo with one look like a pi project (Codex owns that guess).
    context_files = [".pi/APPEND_SYSTEM.md", ".pi/SYSTEM.md"]
    # What pi loads by itself under the project root; the config dir's files
    # are added by `loaded_paths` (their location is an env var, not static).
    harness_loads = ["AGENTS.md", "CLAUDE.md", ".pi/APPEND_SYSTEM.md"]
    # The append file: pi adds its whole content to the system prompt, which
    # is exactly what the assembled context is for.
    context_target = ".pi/APPEND_SYSTEM.md"
    detect_env_vars = ["PI_CODING_AGENT"]
    harness_id = "pi"
    launch_command = "pi"
    vendor = ""
    model_source_vars = ["PI_MODEL"]

    @staticmethod
    def _config_dir() -> Path:
        """``$PI_CODING_AGENT_DIR``, else ``~/.pi/agent`` (the documented default)."""
        return Path(os.environ.get("PI_CODING_AGENT_DIR") or (Path.home() / ".pi" / "agent")).expanduser()

    def loaded_paths(self, project_root: Path) -> "list[Path]":
        out = super().loaded_paths(project_root)
        for name in ("AGENTS.md", "CLAUDE.md", "APPEND_SYSTEM.md"):
            try:
                out.append((self._config_dir() / name).resolve())
            except OSError:
                pass
        return out

    def write_base_config(self, dest: Path, src: Path, base_agents_text: str, *, force: bool, dry_run: bool, logger) -> None:
        from dotagents._merge import BEGIN_MARKER, END_MARKER, merge_block, timestamped_backup_root
        backup_root = timestamped_backup_root(dest) if force else None
        branch = merge_block(
            dest / "AGENTS.md",
            base_agents_text,
            force=force, dry_run=dry_run, backup_root=backup_root,
        )
        if logger: logger.info("%s: AGENTS.md (pi)", branch)
        # The last mile for the user store: pi reads `<config dir>/AGENTS.md`
        # in every session and has no include syntax, so that file gets a
        # managed block POINTING at the store's AGENTS.md -- not a copy of it,
        # which would reach a launched session twice (once from pi's own file,
        # once as the context `launch` appends). A project store needs nothing:
        # pi walks the cwd's parents and finds the project's own AGENTS.md.
        if _is_user_store(dest):
            store_agents = (Path(dest).expanduser().resolve() / "AGENTS.md").as_posix()
            entry = self._config_dir() / "AGENTS.md"
            pointer = (
                "%s\n# dotagents\n\n"
                "Read `%s` before anything else: it is the global agent configuration "
                "`dotagents init` manages (the always-on rules and the routing to "
                "on-demand files).\n%s\n" % (BEGIN_MARKER, store_agents, END_MARKER)
            )
            branch = merge_block(entry, pointer, force=force, dry_run=dry_run, backup_root=backup_root)
            if logger: logger.info("%s: %s (pointer to the store)", branch, entry)

    @staticmethod
    def _is_windows() -> bool:
        """Seam, as on :class:`ClaudeAgent`: tests patch this, not ``os.name``."""
        return os.name == "nt"

    def launch_context_args(self, context_file: Path) -> "Optional[list[str]]":
        # `--append-system-prompt <text>` is the documented append; there is no
        # file form. On Windows `pi` is an npm `.cmd` shim that cmd.exe
        # re-parses, and a newline inside an argument ends the command there,
        # so the multi-line context takes the static route instead
        # (`.pi/APPEND_SYSTEM.md`, which pi appends by itself).
        if self._is_windows():
            return None
        return ["--append-system-prompt", context_file.read_text(encoding="utf-8")]


# Registry of agents
_REGISTRY: dict[str, type[Agent]] = {
    "claude": ClaudeAgent,
    "gemini": GeminiAgent,
    "antigravity": AntigravityAgent,
    "codex": CodexAgent,
    "cursor": CursorAgent,
    "copilot": CopilotAgent,
    "pi": PiAgent,
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
    """Resolve the active agent by precedence.

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
    """Return the standardized ``AGENTS_*`` / ``AGENT`` identity vars.

    Emits ``AGENTS_HARNESS`` (the harness_id, e.g. ``claude-code`` -- NOT the
    short name), ``AGENTS_VENDOR``, ``AGENT`` (= AGENTS_HARNESS, the ecosystem
    marker used by Goose/Amp; agentsmd/agents.md#136), and, when derivable,
    ``AGENTS_MODEL`` (from the adapter's vendor model var).

    Never emits a var it cannot source (no empty ``AGENTS_MODEL=``). Does NOT
    clobber a value already set in ``environ`` -- an explicit user/harness value
    wins -- UNLESS ``explicit`` names the agent: then the identity describes
    THAT agent and overrides whatever ``environ`` carries, since a base-env
    identity is the *running* harness's (the env-loader hook exports
    ``AGENT=claude-code`` into every command a Claude session runs), not a pin.

    ``AGENTS_AGENT`` (a named persona) is not emitted; nothing may branch on it.
    """
    active = resolve_active_agent(environ, explicit=explicit, root=root)
    # Only a KNOWN explicit name overrides: an unknown one falls through to
    # detection above, and the detected identity must not clobber a pin.
    override = explicit is not None and explicit in _REGISTRY

    identity: dict[str, str] = {}

    def _set(key: str, value: "Optional[str]") -> None:
        if value and (override or not environ.get(key)):
            identity[key] = value

    _set("AGENTS_HARNESS", active.harness_id or active.name)
    _set("AGENTS_VENDOR", active.vendor)
    _set("AGENT", active.harness_id or active.name)
    _set("AGENTS_MODEL", active.resolve_model(environ))

    return identity
