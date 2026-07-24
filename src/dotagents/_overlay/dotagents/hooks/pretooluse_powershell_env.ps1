# PreToolUse hook: inject dotagents env into PowerShell tool calls.
#
# Invocation gotcha, verified directly: the settings.json entry referencing
# this script must invoke it as a genuinely SEPARATE spawned process --
#   powershell -NoProfile -NonInteractive -File "<this script>"
# -- NOT `& "<this script>"`. The `&` call operator runs a script IN the
# current process, and [Console]::In there is the outer console's real
# stdin, not the piped hook-input JSON; confirmed empirically ([Console]::In
# read 0 bytes via `&`, 64 correct bytes via `-File`). `ClaudeAgent.wire_hooks`
# writes the command that way -- do not change it to `&` if you edit this.
#
# Why this exists: Claude's $CLAUDE_ENV_FILE persistence mechanism only ever
# applies to the Bash tool (documented as "subsequent Bash commands" in every
# hooks.md mention; verified empirically -- $env:CLAUDE_ENV_FILE is empty
# inside the PowerShell tool). The PowerShell tool gets none of the
# AGENTS_HOME / identity vars / .agents-bin PATH prefix that the SessionStart
# hook writes for Bash. This hook closes that gap independently, without
# relying on Claude Code's Bash-only mechanism at all.
#
# How: PreToolUse fires as its own fresh process per call (confirmed: an
# env var set inside one hook invocation does not survive to the next --
# same as tool-call processes themselves). So there is no way to "persist"
# a var across PowerShell tool calls by setting it in a hook process. The
# only mechanism PreToolUse actually offers for this is `updatedInput`,
# which rewrites the UPCOMING tool call's own arguments before it runs
# (hooks.md, PreToolUse decision control). This hook uses that: it reads
# the guard var directly from the CURRENT env (which DOES reflect whatever
# dotagents wrote into the real Windows user/process environment previously,
# separately from this hook's own process), and if unset, prepends a
# guarded loader to the tool's own command -- so the LOAD happens inside the
# tool call's process, not this hook's, and is skipped on every later call
# once the loader has actually run inside a PowerShell tool process and set
# AGENTS_RUNTIME_SET there.
#
# Whether that guard is actually visible to the NEXT, separately-spawned
# PowerShell tool call is UNVERIFIED (cannot be tested without a real Claude
# Code session running multiple PowerShell tool calls). If it is not, this
# still works correctly -- it just re-loads (and re-pays the dotagents-env
# spawn cost) every call instead of once. The probe log below exists to
# settle this empirically.
#
# Guard name matches the precursor's AGENTS_RUNTIME_SET convention
# (agentic/agents/src/agents/runtime.py, agentic/agents/_overlay/env.py) --
# same purpose, ported forward rather than reinvented.
#
# Field-name uncertainty: the PowerShell tool's tool_input shape is NOT
# documented (absent from hooks.md's per-tool schema list, unlike Bash/Write/
# Edit/Read/etc). This script assumes a `command` string field, mirroring
# Bash's schema and the one field PowerShell tool calls must have to do
# anything -- but this is NOT verified against Claude Code's actual behavior.
# It fails safe: if `tool_input.command` is absent or the tool isn't
# PowerShell, it does nothing and emits no output, so a wrong assumption
# here can only skip the injection, never corrupt a tool call.

$ErrorActionPreference = "Stop"

try {
    $raw = [Console]::In.ReadToEnd()
    $hookInput = $raw | ConvertFrom-Json

    $logDir = Join-Path $env:USERPROFILE ".agents\hooks\logs"
    if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }
    $logPath = Join-Path $logDir "pretooluse_powershell_env.log"

    $toolName = $hookInput.tool_name
    Add-Content -Path $logPath -Value "$(Get-Date -Format o) tool=$toolName guard=[$env:AGENTS_RUNTIME_SET]"

    if ($toolName -ne "PowerShell") {
        # Not our tool -- no output means "no decision", tool proceeds unmodified.
        exit 0
    }

    if ($env:AGENTS_RUNTIME_SET) {
        # Already loaded in an earlier PowerShell tool call's process lineage.
        # Nothing to inject; skip the cost of even checking further.
        exit 0
    }

    $originalCommand = $hookInput.tool_input.command
    if (-not $originalCommand) {
        # Confirms the field-name assumption was wrong, or this call has no
        # command (e.g. some other PowerShell-tool invocation shape). Log it
        # for a follow-up, but do not touch the call.
        Add-Content -Path $logPath -Value "$(Get-Date -Format o) NO command FIELD -- tool_input=$($hookInput.tool_input | ConvertTo-Json -Compress)"
        exit 0
    }

    # Prepend a guarded, one-line loader. It re-checks AGENTS_RUNTIME_SET
    # inside the TOOL CALL's own process (belt-and-suspenders against the hook
    # and the tool call not sharing state), sets it first (so a failing
    # `dotagents env` can't retry every single call), then evaluates
    # `dotagents env --format powershell` output via Invoke-Expression.
    # `.agents/bin` is not guaranteed on PATH yet (that IS what this loads),
    # so the dotagents entrypoint is called by absolute path via $HOME.
    $prefix = 'if (-not $env:AGENTS_RUNTIME_SET) { $env:AGENTS_RUNTIME_SET = "1"; & "$HOME\.agents\bin\dotagents.cmd" env --format powershell 2>$null | Invoke-Expression }; '

    $updatedInput = $hookInput.tool_input.PSObject.Copy()
    $updatedInput.command = $prefix + $originalCommand

    $output = @{
        hookSpecificOutput = @{
            hookEventName  = "PreToolUse"
            permissionDecision = "allow"
            updatedInput   = $updatedInput
        }
    }
    Add-Content -Path $logPath -Value "$(Get-Date -Format o) INJECTING prefix into PowerShell call"
    $output | ConvertTo-Json -Depth 10 -Compress
    exit 0
}
catch {
    # Never let a hook bug break the user's tool call. Log and pass through.
    try {
        Add-Content -Path (Join-Path $env:USERPROFILE ".agents\hooks\logs\pretooluse_powershell_env.log") -Value "$(Get-Date -Format o) HOOK ERROR: $_"
    } catch {}
    exit 0
}
