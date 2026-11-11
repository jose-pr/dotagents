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
in an overlay or a project, per overlay and store. There is one digest per overlay: any change to
the overlay turns its hooks off until you allow the new one, and going back to an earlier digest
needs a new yes. `allow-hooks` asks at the terminal itself (`/dev/tty`, `CONIN$`), refuses to run
inside an agent harness's session and is not an MCP tool; `--no-prompt` is for your own setup
script. The allowance is only as strong as the agent's lack of write access to your user store:
an agent that can write there can write an allowance.

An overlay with a hook that has `on_failure = "refuse"` (a guard) is **not activated** while its
hooks are off, and its guards **fail** meanwhile: a prompt is blocked, a tool call denied, as if
the hook had run and failed (`hook-not-allowed` at the next session start). A guard that is
missing must not let everything through.

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
forwarder script `overlay_hook.py` deployed in your user config directory), so a store with no
hooks starts no extra process. A project's entries (`settings.local.json`) run the same forwarder
from your user config directory. The text of an entry holds no guard flag and no overlay name, so
it does not change when overlays do. Codex asks you to trust a hook by its hash; dotagents never
writes that trust.

## Limits and failures

Every hook runs with the overlay root as its working directory, outside the sandbox, in its own
process group (a job object on Windows) that ends with it, with arguments as a list (no shell
reads the overlay's strings) and the environment of that overlay's own processes. Defaults:
60 s a run (and the entry's `timeout`, default 30, if shorter), 120 s an event, 64 KiB of
output (more stops the run), 64 KiB of standard error kept for the log, 1 MiB of payload. A
failed hook applies nothing of its output; with `on_failure = "report"` the event goes on, with
`"refuse"` it is refused (a prompt blocked, a call denied, a session start reported as not
refusable). `pre-env` stops the session only under `dotagents launch`, where the harness does not
exist yet; under `dotagents env` (a harness already running, a setup) it is reported as
`hook-not-applied` and nothing of the hook applies. An oversize or unreadable payload is a failure
of the event, refused when it has a guard.

The limits are settings, read from your user store only
(`overlays/.dotagents-state/hooks-settings.json`; a project's file is never read), each in
bounds: `timeout`, `event_timeout`, `deadline`, `max_output`, `max_payload`. The deadline
(default 150 s, at most 580) covers everything a harness's hook entry does from the process's
start: building the scope, hashing, env scripts and hooks; at it the whole process tree ends. The
harness's own timeout for the entry is derived from it and written at wiring time, so run
`dotagents init -g` after changing it.

Context a hook adds is wrapped in a marker holding a random nonce chosen after the hooks ran, so
the text cannot imitate it. Variables for Bash are written to Claude Code's env file as
single-quoted values of names the hook declared in `sets`; a value holding a line separator of
any kind (CR, LF, VT, FF, U+0085, U+2028, U+2029, NUL) is not written (`hook-not-applied`), and a
sensitive name (`PATH`, `BASH_ENV`, `LD_*`, ...) that is set is reported (`env-sensitive`). What a
hook writes to standard error goes to `<user store>/.cache/hooks.log` (bounded), never to the
harness.

## What a hook cannot reach

- **Bytes that were not hashed.** At each event the overlay's tree is read once, hashed, compared
  with the allowed digest, and written to a snapshot under `<user store>/.cache/hook-snapshots/`
  from those same bytes; the hook runs from the snapshot. A file swapped after hashing, a
  `node_modules` or `__pycache__` (not part of a digest) planted beside the overlay, do not run.
  The hook's `AOS_OVERLAY_ROOT` and its overlay's `<NAME>_OVERLAY_ROOT` name the snapshot.
- **The project's `dotagents`.** The hook entries run an absolute interpreter, isolated (`-I`),
  a forwarder script in your user config directory, and the `dotagents` that `init` recorded
  there. They never look in the working directory or the project's `.agents/bin`, and the
  forwarder passes the harness only a refusal, context and a message in the harness's own shape:
  an `allow`, a rewritten input or anything else it does not recognize is not passed on (for a
  guard that is a refusal). The session-start, PowerShell, Codex and Antigravity entries that
  dotagents wrote before also run the store's `dotagents` or PATH's, not the project's.
- **A project's Python.** A hook runs with the user store's Python, never a project's venv.
- **Other overlays' secrets.** `dotagents launch` records what it changed in
  `AGENTS_SESSION_BASE`; a hook starts from the environment the session was started from, so a
  secret another overlay was granted does not reach it. The harness's own variables
  (`CLAUDE_ENV_FILE`, `CLAUDE_CODE_*`, `CODEX_*`) are dropped too.

Limits of this: a hook that detaches into a new session (`setsid`) can outlive the tree kill on
POSIX; an overlay whose manifest is refused cannot show its guards; a hook can still read what
its overlay `requires` puts on its paths, live; an entry whose interpreter has been uninstalled
cannot run to say so (`dotagents overlays hooks` and every `init` check the recorded one); and
Codex asks you to trust an entry by its hash, which dotagents cannot read: `overlays hooks` says
the event fires natively once you have trusted it, and a change of the entry text is warned
about when it is wired.

## Not done yet

Hooks declared by your own configuration or a project's (outside overlays), a `pre-tool`
variable loader, `OVERLAY.md` front matter, and harnesses other than Claude Code and Codex.
