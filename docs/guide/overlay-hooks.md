# Overlay hooks

An overlay can run a program at a named moment of a session: before the environment is built,
when a session starts or ends, when you send a prompt, before a tool runs. This is the
[Agent Overlay Specification's hooks](https://boriken-dev.github.io/agent-overlay-spec/spec/v0/hooks/)
(an optional extension); this page is what dotagents does with them.

```toml
# overlay.cfg
[hooks]
session-start = "hooks/start.py"
pre-tool = { run = "hooks/guard.sh", match = ["execute"], timeout = 5, on_failure = "refuse" }
turn-end = false
```

A `hook.<ext>` at the overlay root (chosen by runtime, as `env.<ext>` is) receives every event
that carries no content by default. `prompt`, `pre-tool` and `post-tool` carry what you typed or
what a tool was given, so they fire only for an event `[hooks]` names. The hook is told its event
as its first argument and in `AOS_HOOK_EVENT`, and its payload as one JSON object on standard
input; it answers with nothing or one JSON object (`context`, `decision`, `reason`, `env`).

## A hook tightens and never grants

Nothing a hook prints reaches the harness. dotagents reads the output, accepts only what the
event takes, and answers the harness itself, in the harness's words. A `decision` other than
`block`, `deny` or `ask` (`allow`, `approve`, anything) is a refused attempt to grant: the run
fails (`grant`) and nothing of it applies. dotagents never states an approval and never rewrites
a tool's input.

## Off until you allow them

A hook is the overlay's code, running at every session with your access, outside the harness's
sandbox. An overlay's hooks do not run until you allow them at the overlay's digest:

```
dotagents overlays hooks NAME                       # what runs, where, and whether it is allowed
dotagents overlays allow-hooks NAME                 # shows the same and the digest
dotagents overlays allow-hooks NAME --digest sha256:...
dotagents overlays revoke-hooks NAME
```

The allowance is kept in your user store (`overlays/.dotagents-state/hooks-allowed.json`), never
in an overlay or a project. There is one digest per overlay: any change to the overlay turns its
hooks off until you allow the new one, and going back to an earlier digest needs a new yes. An
overlay with a hook that has `on_failure = "refuse"` (a guard) is **not activated at all** while
its hooks are off, since a guard that is missing would fail open without a word.

## Which events fire where

| Event | Claude Code | Codex | Any other harness |
| --- | --- | --- | --- |
| `pre-env` | dotagents fires it as it builds the environment | same | same |
| `session-start` | `SessionStart`: context, and variables for Bash through the env file | `SessionStart`: context | cannot |
| `session-end` | `SessionEnd` | `SessionEnd` | cannot |
| `prompt` | `UserPromptSubmit`: context, block | same | cannot |
| `turn-end` | `Stop` (notification only) | `Stop` | cannot |
| `cd` | `CwdChanged`: variables | no such event: cannot | cannot |
| `pre-compact` | `PreCompact`: context, block | `PreCompact`: block (no context) | cannot |
| `pre-tool` | `PreToolUse`: deny, ask, context | `PreToolUse`: deny, context (`ask` is applied as `deny`) | cannot |
| `post-tool` | `PostToolUse`: context | `PostToolUse`: context | cannot |

What cannot fire is reported (`hook-unsupported`), as is an event that fires for only some
occurrences (`hook-partial`: Claude Code's `PostToolUse` is for calls that ended well; Codex's
tool hooks were measured on its shell tool), an output the harness cannot take
(`hook-not-applied`: variables from `prompt` or `pre-tool`, context from Codex's `pre-compact`,
refusing a session start), an `ask` turned into a `deny` (`hook-ask-denied`), a hook that is off
(`hook-not-allowed`), and a failed run (`hook-failed`, with its cause, never its output). The
reports reach you in the session-start message of the harness and in the log of `dotagents env`.
`dotagents overlays hooks` lists the table above per hook.

`init` and `overlays add/sync/remove` wire the harness's own hook for an event only when an
installed overlay has a hook for it (`dotagents hook` is the program they run, through a small
forwarder script `overlay_hook.py` deployed beside the harness's hook configuration), so a store
with no hooks starts no extra process. Codex asks you to trust a hook by its hash; dotagents
never writes that trust.

## Limits and failures

Every hook runs with the overlay root as its working directory, outside the sandbox, in its own
process group (a job object on Windows) that ends with it, with arguments as a list (no shell
reads the overlay's strings) and the environment of that overlay's own processes. Defaults:
60 s a run (and the entry's `timeout`, default 30, if shorter), 120 s an event, 64 KiB of
output (more stops the run), 64 KiB of standard error kept for the log, 1 MiB of payload. A
failed hook applies nothing of its output; with `on_failure = "report"` the event goes on, with
`"refuse"` it is refused (a prompt blocked, a call denied, a session start reported as not
refusable, `pre-env` stopping the session).

Context a hook adds is wrapped in a marker holding a random nonce chosen after the hooks ran, so
the text cannot imitate it. Variables for Bash are written to Claude Code's env file as
single-quoted values of names the hook declared in `sets`.

## Not done yet

Hooks declared by your own configuration or a project's (outside overlays), a `pre-tool`
variable loader, `OVERLAY.md` front matter, and harnesses other than Claude Code and Codex.
