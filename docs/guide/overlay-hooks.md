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

## Allowed by installing

A hook is the overlay's code, running at every session with your access, outside the harness's
sandbox. Installing an overlay is choosing to trust it, hooks included: `overlays add` and
`overlays sync` allow the overlay's hooks at the digest they install and log which are on (event and
script). A sync that changes the digest allows the new one, since you chose the source.

```
dotagents overlays add NAME [--no-hooks]            # installs, and allows the hooks unless --no-hooks
dotagents overlays sync [NAME] [--no-hooks]
dotagents overlays hooks NAME                       # what runs, where, and whether it is allowed
dotagents overlays revoke-hooks NAME                # off, and a sync keeps them off
dotagents overlays allow-hooks NAME                 # shows the same and the digest, then asks at the terminal
dotagents overlays allow-hooks NAME --digest sha256:...
```

`--no-hooks` installs without allowing them, and a later `sync` leaves them off (it is remembered
like a revoke). `revoke-hooks` turns them off and a `sync` keeps them off until `allow-hooks` or a
fresh `overlays add`. An overlay installed by 0.9.2, whose hooks were off, is allowed by its next
`overlays sync`, unless it was revoked.

Hooks run **outside the agent's sandbox**, with your access. An agent that can write the project's
`.claude/` (its own `settings.local.json`) or your user store can run code outside its sandbox
anyway, so a hook is nothing it could not already add: installing an overlay is the trust decision,
and `dotagents overlays add` and `sync` make it once. The default `dotagents mcp` server behaves as
the command line does, so an add or sync an agent asks for through it allows hooks too. Start it
**`dotagents mcp --strict`** (or put `{"strict": true}` in `overlays/.dotagents-state/mcp.json` of
your user store, which a server started by `DOTAGENTS_MCP` reads) for a stricter server: an add or sync
through it installs without allowing hooks, says the hooks are off and that `allow-hooks` at a terminal
turns them on, and records nothing (what you allowed before stays allowed at its digest). The setting
is read from the user store only; a project's files cannot make a server strict or lax.

The allowance is kept in your user store (`overlays/.dotagents-state/hooks-allowed.json`), never
in an overlay or a project, per overlay and store. There is one digest per overlay: a change to the
overlay that did not come through `overlays sync` or `add` (a hand edit, an update by other means)
turns its hooks off until you allow the new digest, and going back to an earlier digest needs a new
yes. `allow-hooks` asks at the terminal itself (`/dev/tty`, `CONIN$`), refuses to run inside an
agent harness's session and is not an MCP tool; `--no-prompt` is for your own setup script. The
allowance is only as strong as the agent's lack of write access to your user store: an agent that
can write there can write an allowance, and one that can run `dotagents overlays add` through a shell
tool installs and allows as you would.

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

This is about the **overlay-hook path**: a hook of an installed overlay, fired through the
forwarder (or by `pre-env`). It is the hardened path. It is not the whole of dotagents: see
"Where project code does run" below.

- **Bytes that were not hashed.** At each event the overlay's tree is read once, hashed, compared
  with the allowed digest, and written to a snapshot under `<user store>/.cache/hook-snapshots/`
  from those same bytes. The manifest the event is planned from, the env scripts that build the
  hook's environment, the `lib/` and `bin/` paths they declare, the hook, and its working
  directory are all the snapshot's; Python hooks run under `-I` with the snapshot's libraries on
  the path explicitly. A file swapped after hashing, or a `node_modules`, `__pycache__` or
  `.venv` (not part of a digest) planted beside the overlay, do not run. An existing snapshot is
  hashed again before it is used, and copies of other digests are removed only when unused for a
  week (a session started before an update still runs from its own). The overlay's state
  directory is the live one, so a hook keeps what it wrote.
- **Anything the project holds.** The hook entries run an absolute interpreter, isolated (`-I`),
  a forwarder script in your user config directory, and a fixed entry of the `dotagents` that
  `init` recorded there (`dotagents._hook_main`). That entry is not the `dotagents` command line:
  it discovers no command and adds no library, so `<project>/.agents/dotagents/cmds/`, a
  project overlay's `cmds/` and `lib/` and the project's own modules never load in it. For a
  user-level hook its scope has no project store at all, so a project folder cannot shadow a hook
  of yours (a same-named one is reported, `hook-shadowed`, and your hook still runs), cannot add
  anything to it, and cannot choose its Python.
- **A stand-in for dotagents.** The forwarder gives the entry a fresh nonce and accepts only an
  answer that carries it back. A program that exits 0 with no output, or answers without the
  nonce, is a failed guard (a refusal) and nothing at all for an event with no guard; an
  "everything is fine" is never inferred from silence. When the forwarder's record lists a guard
  for an event and the entry finds none, that is a refusal too. The forwarder passes the harness
  only a refusal, context and a message, in the harness's own shape.
- **A project's interpreters.** `pwsh`, `node`, `bash` and the like, for a hook and for the env
  scripts that build its environment, are found by dotagents itself (never the current
  directory, which Windows searches first) on the `PATH` the session was started from, less every
  relative entry, store or overlay `bin`, venv, and anything below the project or the user store
  (`.venv/Scripts`, `node_modules/.bin`, `.agents/bin`). A Windows bash is looked for only beside
  a `git` on that PATH and in the standard install directories.
- **A project's Python.** A hook, and the env scripts that build its environment, run with the
  user store's Python chosen before any declaration runs, never a project's venv, and never the
  session's `AGENTS_PYTHON`.
- **Other overlays' secrets.** `dotagents launch` records what it changed in
  `AGENTS_SESSION_BASE`; a hook starts from the environment the session was started from, so a
  secret another overlay was granted does not reach it. The harness's own variables
  (`CLAUDE_ENV_FILE`, `CLAUDE_CODE_*`, `CODEX_*`) are dropped too. The project's own name for
  itself (`AGENTS_PROJECT_ROOT`, `CLAUDE_PROJECT_DIR`) is kept.
- **The harness itself.** `dotagents launch` looks for a bare program name (`claude`) on the PATH
  the shell had, less the same places, not on the session's PATH that starts with the project's
  `.agents/bin`.

## Where project code does run

Not everything dotagents does is hardened this way, by design (the owner's decision, recorded in
dotagents' decisions):

- The session-start, Codex and Antigravity entries that `init` wrote before overlay hooks existed
  run `dotagents env` and `dotagents context` **in the project**. Those are the full command line:
  they load `<project>/.agents/dotagents/cmds/`, and `env` runs the env scripts of the overlays
  active there, outside the harness's sandbox. They run the store's `dotagents` (never one found in
  the project or on PATH), but what that program loads from the project, it runs.
- A project overlay's own `env`/env scripts run whenever the environment is built for a session
  there (`env`, `launch`, `setup`), as any active overlay's do.
- **Project-level guards are advisory.** A project-level hook entry lives in
  `<project>/.claude/settings.local.json`, which the agent can write: it can remove the entry. The
  forwarder and the user-level entries in your own config are not affected, but a guard that only
  a project overlay declares is only as strong as that file. Put a guard you rely on in an overlay
  of your user store.

An agent that can write the project's `.agents`, or your user store, can run code through those
paths; the overlay-hook path is the one that does not depend on either being safe from the agent.

## The limit, generally

All of this assumes the places dotagents runs code *from* are not the agent's to write. A
deployment gets no barrier where:

- the recorded `code_root` is inside a checkout the agent works in (a `dist/dotagents.pyz` of a
  repository, a source tree on `PYTHONPATH`), or a venv's `.pth` points dotagents into such a
  place: what the agent writes there runs with your access. Install dotagents where the agent
  cannot write, then run `dotagents init -g`;
- the agent can write your user store (`~/.agents`): it holds the allowances, the snapshots, the
  settings and the wrappers, so it can allow and run anything.

`--no-prompt` and the harness-session check of `allow-hooks` stop accidents, not an agent: an
agent can clear its environment (`env -i`) and run the command. What keeps an agent from allowing
its own hooks is that `allow-hooks` is not an MCP tool and that the allowance is in a store it
cannot write.

Smaller limits: a hook that detaches into a new session (`setsid`) can outlive the tree kill on
POSIX; an overlay whose manifest is refused cannot show its guards; a hook can still read what
its overlay `requires` puts on its paths, live, and the packages an overlay's setup installed; an
entry whose interpreter has been uninstalled cannot run to say so (`dotagents overlays hooks` and
every `init` check the recorded one); and Codex asks you to trust an entry by its hash, which
dotagents cannot read: `overlays hooks` says the event fires natively once you have trusted it,
and a change of the entry text is warned about when it is wired.

## Not done yet

Hooks declared by your own configuration or a project's (outside overlays), a `pre-tool`
variable loader, `OVERLAY.md` front matter, and harnesses other than Claude Code and Codex.
