# dotagents — package API header

The public API of the installed `dotagents` package (distribution `dotagents-cli`,
Python ≥ 3.9): the `dotagents` command line, the `dotagents.cli` extension API, and
the `_*.py` helper modules behind them. Read this instead of the source. Full docs:
https://jose-pr.github.io/dotagents/

Terms used throughout:

- **store** — an `.agents`-shaped directory: the **user** store (`$AGENTS_HOME`, else
  `~/.agents`), a **project** store (`<project>/.agents`), or the **system** store
  (`$AGENTS_SYSTEM_ROOT`, else `/etc/agents` on POSIX; none on Windows; used only when
  it exists and only administrators can write it).
- **scope** — which store a command writes to (`user` with `-g`, else `project`) plus
  the stores a session reads (a `Scope`).
- **contract A** — the precedence walk: store by store (system, user, project), each
  store's installed overlays first, then the store itself; then the project root.
- **overlay** — an AOS overlay (the Agent Overlay Specification, edition
  `0.1.0-draft`, https://boriken-dev.github.io/agent-overlay-spec/): a directory,
  optionally with an `overlay.cfg` manifest, installed as `<store>/overlays/<name>/`;
  its root is exported as `$<NAME>_OVERLAY_ROOT`. Its conventional files take
  effect by default, when present (a member file there): `bin/` prepended to
  `PATH`, `lib/python/` prepended to `PYTHONPATH`, the root `env` read as a dotenv
  env file, one `env.<ext>` as its first env script and one `setup.<ext>` as its
  last setup variant (the first that applies and can run, in the order `.py`,
  `.js`, `.mjs`, `.cjs`, then `.ps1` on Windows, then `.bash`, `.sh` elsewhere),
  `CONTEXT.md` as context, `skills/<name>/SKILL.md` as skills and
  `rules/<name>.md` as rules files. `[defaults]` turns one off (`bin`, `lib`,
  `env`, `env_scripts`, `setup`, `context`, `skills`, `rules = false`; any other
  key is refused). What the manifest declares adds to the defaults, and an entry
  naming a default's file or directory replaces that default entry. A bare
  `lib/`, `pre.env*`, `local.env`, `requirements.txt` and `bin/<name>.py` as a
  command mean nothing.
- **active overlay** — an installed overlay that is not refused: one whose store
  pins it (`_policy`) is active only while its tree digest is the pin.

`dotagents.__version__` is the package version.

## Command line

`dotagents <command> [options]` (also `python -m dotagents`, or a built `.pyz`).
`dotagents --help` / `<command> --help` list every flag. Umbrella options:
`--cmdspath DIR` (repeatable), `--loglevel`, `-v` / `-q`. An umbrella run with no
subcommand (`dotagents`, `dotagents overlays`, `dotagents findings`) prints its usage
and an error saying a command is required, and exits 2.

| Command | Contract |
| --- | --- |
| `init [-g] [--dest D] [--from SRC] [--bin-dir D] [--agents a,b] [--no-hooks] [--powershell-env-hook] [--no-venv] [--force] [--dry-run]` | Merge the base block into `<store>/AGENTS.md`, write `<store>/bin/dotagents[.cmd]`, and per active agent (`--agents`, else detected + `claude`) write its include and hooks. Then bring the scope's Python venvs up to date (`_pyvenv.sync_venvs`; a failure is a warning; not with `--dest` or `--no-venv`). `--force` replaces `AGENTS.md`'s content, backed up under `<store>/install_backup/<timestamp>/`. `--from` records its base in `<store>/dotagents/config.toml`. |
| `overlays add NAME\|GLOB... [-g] [--repo SPEC]... [--copy] [--no-setup] [--setup-timeout S] [--no-requires] [--no-venv] [--dry-run]` | Install each overlay and its `requires` into `<store>/overlays/<name>/` (an installed one is made to match its source again, as `sync` does), publish its skills, bring the scope venv up to date (`_pyvenv`; not with `--no-venv`), prepare it -- its dependency files built into their environments (`_deps`), then its `setup`, in the phase `install` the first time a preparation succeeds here and `sync` after that (`--no-setup`: neither) -- and recompose the managed block. Before anything of an overlay is written it is refused when its `overlay.cfg` does not validate (named files included), its tree has no digest (a link, a refused name), its git commit holds a link or submodule, this machine lacks what its `runtime`, setup or scripts need, or its store pins another digest. A glob (`'*'`, `'py*'`) stands for every name the repos offer that matches it; one matching nothing is an error. Every name is resolved before anything is written; a source inside `<store>/overlays/` is refused. An overlay whose copy, venv or preparation fails is logged and skipped, along with what requires it; the others still install, and the command exits 1 naming each failure. One this run was installing for the first time is removed again (its environments rebuilt without it); one already installed stays, activated only for the digest it was last prepared for. No name: exit 2. |
| `overlays remove NAME... [-g] [--force] [--no-setup] [--setup-timeout S] [--no-venv] [--dry-run]` | Run each overlay's setup in its `remove` phase when a preparation succeeded here (a failure is reported and the removal goes ahead), delete the overlay dirs, their state and preparation records, rebuild the environments they were in without them (an isolated one, or one left empty, deleted), unpublish the skills they published, recompose the block, rewrite the venv layering (installed requirements stay). Refuses an overlay another installed overlay requires unless `--force`. No name: exit 2. |
| `overlays sync [GLOB] [-g] [--repo SPEC]... [--copy] [--no-setup] [--setup-timeout S] [--no-venv] [--dry-run]` | Refresh installed overlays from the repo recorded at install (`--repo` replaces it), checked as `add` checks a source: the install is made to hold exactly the overlay's members (new and changed files land, everything else goes, caches and setup output left inside it included); a file edited here is backed up first under `<store>/install_backup/<timestamp>/overlays/<name>/`. Installs new `requires`, then prepares each synced overlay in its `sync` phase (`install` when no preparation succeeded here yet), whether its digest changed or not: its environments rebuilt, then its setup. Exits 1 when an overlay's repo cannot be loaded, its source lies inside `<store>/overlays/` or is refused, or its dependencies or setup fail. |
| `overlays list [-g] [--repo SPEC]... [--json]` | Installed overlays per store (shadowed copies marked, unmet `requires` and refused overlays flagged) and what the repos offer. |
| `overlays show NAME [-g] [--repo SPEC]... [--json]` | One overlay: the copy a session would use, else the source's; its declarations, a refused manifest's problems, tree digest, pin, what this machine lacks for it, its weakenings, skills, member count, recorded source and commit. No name: exit 2. |
| `overlays hooks [NAME] [-g]` | An installed overlay's hooks (every overlay with some, without a name): per event the script and its settings, whether the hooks are allowed at the overlay's current digest, and per harness whether the event fires natively, is fired by dotagents itself (`pre-env`) or cannot fire. |
| `overlays allow-hooks NAME [--digest D] [--no-prompt] [-g]` | Show the same, then allow the overlay's hooks at digest `D`, which must be the current one: without `--digest` it prints the digest to pass and allows nothing (exit 0); a wrong digest exits 2. Then it asks at the terminal itself (`/dev/tty`, `CONIN$`): `allow NAME` to confirm; `--no-prompt` is for the user's own setup script. It refuses (exit 2) inside an agent harness's session and is not an MCP tool. The allowance is kept in the user store (`overlays/.dotagents-state/hooks-allowed.json`), one digest per overlay and store, and is only as strong as an agent's lack of write access to the user store. |
| `overlays revoke-hooks NAME [-g]` | Take back the allowance. |
| `hook EVENT --harness claude-code\|codex [--level user\|project] [--guard]` | Not for typing: what a harness's own hook entry runs (through `overlay_hook.py`). Reads the harness's payload on standard input, fires the overlays' hooks for the event (`user`: the user and system stores' overlays, `project`: the project store's), and answers in the harness's words; a hook's output never reaches the harness and the answer is never an approval. `--guard`: a failure of this program answers with a refusal where the event can be refused. Always exits 0 (2 for a bad argument). Not an MCP tool. |
| `context [OUT] [-g] [--agents a,b] [--format markdown\|system-reminder\|json] [--write-agent] [--inline]` | Print (or write to `OUT`) the assembled context. `--write-agent` merges it into each agent's `context_target` under the project root (not with `-g`). Exits 2 when `--agents` names no known agent. |
| `path [-g] [--lib] [--format list\|native\|posix\|json]` | Print the dirs `env` adds to `PATH` -- each store's `bin` and each active overlay's `PATH` additions (`--lib`: the existing agent library dirs, `PYTHONPATH`) -- highest precedence first; runs no env file. `posix` is `:`-joined, MSYS2 form on Windows. |
| `env [-g] [--format F] [--diff] [--cache] [--into FILE]` | Print the assembled environment (`--diff`: only what differs from the caller's; `--cache`: replay the previous output while `env_cache_key` holds, up to `ENV_CACHE_TTL`; `--into FILE`: write it into FILE as the one block this command owns there) in format `F`: `auto` (default: the calling shell), `export`/`posix`/`sh`/`bash`, `dotenv`/`env`, `powershell`/`pwsh`/`ps`, `cmd`/`bat`/`batch`, `fish`, `json`, `ini`, `yaml`. |
| `launch [AGENT] [-g] [--command PROG] [--no-context] [--inline] [--write-agent] [--dry-run] [-- ARGS...]` | Run the agent's CLI with the env applied and the context handed over; the exit code is the harness's (`128+N` on signal N). |
| `findings add\|list\|show\|done\|reopen\|remove\|index\|path [-g] [--dir D]` | The findings queue at `<store>/findings/` (`--dir` overrides): one markdown file per finding, `done` moves it to `processed/` with a required resolution, `INDEX.md` regenerated on every change. The frontmatter is valid YAML (a value is double-quoted when it must be); `index --requote` fixes values written bare in existing files, touching nothing else. |
| `about [--json]` | `dotagents-cli <version>`, then `<distribution> <version>` per bundled (`.pyz`) or installed package. |
| `build-pyz [--out P] [--python SHEBANG] [--duho-version V] [--pathapi-version V] [--extras a,b] [--with-pip]` | Build a zipapp from a source checkout; errors elsewhere. `--with-pip` vendors the pinned pip wheel (`_pipwheel`) as `dotagents/_vendor/`. |
| `mcp [--prefix TEMPLATE]` | Serve command and active overlay MCP tools over stdio; `{name}` in the template is the skill or server name. |

**Every command but `env` and `mcp` is also an MCP tool.** `dotagents mcp` (or
`DOTAGENTS_MCP=stdio dotagents`, the same server) serves the command set — built-in,
bundled and discovered — over stdio: a tool per leaf command, named with dots
(`dotagents.findings.add`, `dotagents.overlays.list`), its input schema generated from
the command's fields, its result the command's output. `env` is not served: it prints
resolved, possibly secret values. The command set is fixed when the server starts, for
the scope it starts in. With `AGENT_HELP=1`, `--help` prints one
JSON document (`duho/agent-help@1`) for the command tree it is asked about, in place
of the human text.

The same server forwards the tools of the MCP servers active overlays declare in
`overlay.cfg` (`[[mcp.servers]]`, `[[mcp.skills]]`; keys in
`docs/guide/overlays.md`). Forwarded names are `<skill-or-server-name>.<tool>`, or
`--prefix`'s template with `{name}` filled in, plus `.<tool>`. Gotchas:

- An invalid `[mcp]` table in any active overlay stops the server from starting
  (exit 2, the reason on stderr).
- Overlay servers start on the first `tools/list`; one that fails to start, or a tool
  whose name collides with one already listed, is left out with a warning.
- A stdio server's environment is `PATH`, `HOME`, `USERPROFILE`, `SYSTEMROOT`,
  `TEMP`, `TMP`, the variables named in `env_passthrough`, then `env`. An HTTP
  server's credentials come only from the variables `headers_env` names.
- Streamable HTTP needs the `mcp-http` extra (`syshttp`).
- `dotagents.mcp.refresh` re-reads the declarations, closes every connection and
  sends `notifications/tools/list_changed`; the next `tools/list` reconnects.
- Only tools are forwarded: not resources, prompts, or a server's own requests.

Scope flags: `-g` / `--global` selects the user store; `--agents-dir D` overrides the
store the command resolves to (the project store for `init` / `overlays` / `findings`
without `-g`, the user store otherwise). For `env`, `context` and `launch`, `-g` means
"skip the project tiers" and the store is always the user store.

Project root: `init`, `overlays` and `findings` use `install_project_root()`; `env`,
`context` and `launch` use `project_root_default()` (see `dotagents._scope`).

Command discovery, later source wins on a name: built-ins < bundled `findings` /
`launch` < per store in contract-A order (system, user, project), each store's
overlays' `cmds/` then the store's `dotagents/cmds/` < `$AGENTS_CMDS_PATH` entries <
`--cmdspath`. The project root's own `dotagents/cmds/` is never a source. A module
whose import raises, or a command whose parser cannot be built, is skipped with a
warning naming its file. Every existing overlay/store `lib` is appended to
`sys.path` before the modules import, so a command imports them from any shell. Only module-level command classes register; `_*.py` files
are skipped.

## `dotagents.cli`

- `main(argv=None) -> int` — the entry point (`dotagents` console script,
  `python -m dotagents`, `install.py`). It always returns the status and never
  raises `SystemExit`: a `SystemExit` raised below it (a message, `--help`, a usage
  error) comes back as its status, the message going to standard error. Ctrl-C
  returns 130 and a closed pipe on standard output returns 1, both without a
  traceback.
- `class Dotagents(LoggingArgs, Cli)` — the umbrella; field `cmdspath: list[str]`.
- `CMDS_PATH_ENV = "AGENTS_CMDS_PATH"`.
- Re-exported for command modules: `DotAgentsArgs`, `_write_stdout` (see below).
- `kebab_name(class_name) -> str` — a discovered command's default name when its
  class declares no `_parsername_`: `LeakCheck` -> `leak-check`, `HTTPServer` ->
  `http-server`, `my_cmd` -> `my-cmd`. `_parsername_` is optional in a command
  module; an explicit one always wins.

## `dotagents.cli._common`

- `class DotAgentsArgs(LoggingArgs, Cmd)` — base for a scope-aware command: fields
  `global_scope: bool = False` (`--global`, `-g`) and
  `agents_dir: Optional[Path] = None` (`--agents-dir`); `resolve_scope(*,
  project_root=None) -> Scope` (via `_scope.resolve_scope`). Subclass it alone
  (`class X(DotAgentsArgs)`), define `__call__(self) -> int` and optionally
  `_parsername_` (default: `kebab_name` of the class, for a discovered module). A
  subclass may redeclare a field with the same type and default to change its help.
- `_write_stdout(text) -> None` — write to stdout as UTF-8 whatever the console's
  encoding (a bare `print` fails on a non-Latin-1 character under cp1252).
- `STORE_CONFIG = "dotagents/config.toml"`;
  `read_store_config(dest) -> dict[str, str]` (`{}` when absent or invalid);
  `write_store_config(dest, values) -> Path`.
- `store_base(dest, logger=None) -> Path` — the base recorded by `init --from`, else
  `BASE_ROOT` (also when the recorded one no longer resolves, with a warning).
- `recorded_from(from_arg, logger=None) -> str` — how `--from` is recorded: a local
  path made absolute, anything else with its credentials and query values removed
  (said once when they were).
- `CHECKOUT_BASE = "src/dotagents/_overlay"` — where `--from <dotagents checkout>`
  finds the base.
- Re-exported from their own modules, for the command modules and tests that import
  them from here: `BASE_ROOT`, `BASE_AGENTS_TEMPLATE`, `BASE_PROJECT_TEMPLATE`,
  `AGENTS_MD_PLACEHOLDER`, `base_agents_text` (`dotagents._resources`),
  `OVERLAY_ROOT_NOTE` (`dotagents._overlays`), `resolve_user_store`
  (`dotagents._scope`).

## `dotagents._aos`

The AOS format, pure and filesystem-free except for the tree digest: what
`overlay.cfg` and env files read as, validation, the tree digest, and applying
one overlay's `env`, `env_files` and `paths`. Passes the specification's
conformance vectors (`tests/aos_vectors/`, revision in `REVISION`).

- `EDITION = "0.1.0-draft"`. `FORMATS` (format-line name → format),
  `supported_formats() -> list[str]` (`toml`, `json`, and `yaml` when PyYAML
  imports), `DEFAULT_MAX_BYTES = 1 MiB`, `DEFAULT_MAX_DEPTH = 64`.
- `class ReadError(ValueError)` — `code` is the vectors' class (`parse-error`,
  `null-value`, `duplicate-key`, `unsupported-format`, `too-large`, ...).
  `select_format(data) -> (name, payload)`; `read_document(data, *, max_bytes,
  max_depth) -> dict` — the format line (`#!json`, `#!yaml`; TOML by default),
  static reading, the data model (no null, date, non-finite float, integer outside
  64 bits; YAML without tags, anchors, merge keys, directives).
  `parse_dotenv(data) -> list[(name, text, literal)]` — the `dotenv` env-file
  grammar, `literal` for a single-quoted value; `read_dotenv(data, env=None, *,
  windows=False, secret_variables=()) -> dict[str, str]` — the file applied over
  `env`, each line substituted against what the earlier lines left.
- Substitution: `substitute(text, lookup) -> str` — `${NAME}` and its operators
  (defaults and alternates, `${#NAME}`, slices in code points, replacements, trims,
  case), `$$` for `$`; `$NAME`, `$(...)` and backticks are characters; nothing runs
  and an inserted value is never read again. `lookup(name)` gives a value or None
  and raises for a secret. `class SubstitutionError(ReadError)` (`code`:
  `undefined-variable`, `secret-placeholder`, `invalid-pattern`; `name`: the
  placeholder's variable or the pattern).
- Names: `valid_overlay_name`, `normalize_name`, `root_variable(identity)`,
  `state_variable(identity)` (`<NAME>_OVERLAY_ROOT` / `_STATE`),
  `RESERVED_OVERLAY_NAMES`, `DEVICE_NAMES`, `OVERLAY_NAME`, `valid_relpath(path)`
  (the manifest's relative path), `EXCLUDED_COMPONENTS`, `valid_variable_name`,
  `is_reserved_variable` (`AOS_*`, `*_OVERLAY_ROOT`, `*_OVERLAY_STATE`),
  `is_sensitive(name)` with `SENSITIVE_NAMES` / `SENSITIVE_PREFIXES` /
  `SENSITIVE_SUFFIXES` / `PATH_VARIABLES`.
- `validate(doc, files=None) -> list[tuple[code, pointer]]` — the schema and the
  semantic rules; `files` (the overlay's relative paths) also requires every named
  file, listed skill and MCP skill to exist. `named_files(doc)`. `TIMEOUT_MAX`,
  `SCRIPT_EXTENSIONS`, `script_interpreter(target)`.
- Digest: `class DigestError(ValueError)` (`symlink`, `special-file`,
  `non-utf8-name`, `invalid-name`, `case-collision`), `sha256_digest(data)`,
  `file_digest(path)`, `same_digest(a, b)` (hex without regard to case; `ValueError`
  for another form), `DIGEST_FORM`, `EMPTY_DIGEST`, `digest_entries(entries) ->
  (digest, lines)`, `tree_entries(root)`, `tree_digest(root) -> str`,
  `members(root) -> list[str]`, `member_paths(root) -> list[str]` (the same paths
  without hashing or raising: what decides the defaults), `foreign_entries(root) -> list[str]` (excluded
  names present: a mismatch in a verified copy).
- OS and variants: `UNIX_NAMES`, `INTERPRETERS`, `current_os()`, `os_matches(entry,
  os)`, `first_variant(variants, os)`, `version_tuple(text)`.
- `check_requirements(text, *, locked) -> list[str]` — a requirements or lock
  file: requirement lines only (no option, URL or path), a lock pinned with `==`
  and `--hash=sha256:`.
- Applying: `class Refused(Exception)` (`code`, `subject`, `detail`; never a
  value), `class Environment(values, windows, *, secret=(), extra=None)` (names
  case-blind on Windows: `key`, `get`, `set(name, value, *, secret=False)`,
  `unset`, `copy`, `lookup(name)` — what a placeholder reads: `extra` first,
  raising `SubstitutionError` for a variable in `secret`; `assigned`: the
  variables set, in order), `resolve_path(root, rel, windows)`,
  `reference_name(entry)`, `is_reference(entry)`, `class Secrets(*, grants,
  host_env, named, read_file, providers, provider_values, windows)` (`granted`,
  `resolve`: a reference resolves only when granted), `class Context(*, os_name,
  root, identity, read, secrets=None, env_scripts_enabled=True, files=(),
  interpreters=(), state=None, packages=None)` (`files`: the overlay's member
  paths, which decide its defaults; `interpreters`: those the consumer can run;
  `state`: its state directory; `packages`: runtime → its environment's packages
  directory; `placeholders()` — the `AOS_OVERLAY_ROOT`, `AOS_OVERLAY_NAME` and
  `AOS_OVERLAY_STATE` its placeholders see).
- Defaults: `DEFAULT_KEYS` (with `deps`), `DEFAULT_PATHS` (`bin` → `PATH`,
  `lib/python` → `PYTHONPATH`, `lib/node` → `NODE_PATH`, each prepended),
  `DEFAULT_DEPENDENCIES` (`lib/python/requirements.txt`, `lib/node/package.json`
  with the `package-lock.json` beside it), `PACKAGE_VARIABLES` (runtime → the
  variable its packages go on), `DEFAULT_SCRIPTS` (the extension order with the
  OSes each applies on), `default_on(manifest, key)`, `default_paths(manifest,
  files)`, `paths_of`, `rules_of`, `context_of`, `skills_of`, `env_files_of`
  (each `(manifest, files)`: the field's defaults that are on and present, then
  its declared entries; a declared entry naming a default's file or directory
  replaces it; `skills` adds), `default_script(manifest, field, stem, files,
  os_name, interpreters) -> (entry, configured, present, others)` (the default
  `env.<ext>` / `setup.<ext>`; a declared entry naming one configures it in
  place), `setup_of(manifest, files, os_name, interpreters) -> (variant,
  not_run)` (declared variants first, the default last, a default setup taken to
  need the network unless configured).
- Applying: `apply_paths(env, paths, ctx, weakenings)`, `read_env_file(item, ctx,
  listed)` (an `aos` file's mapping, or a `dotenv` file's `parse_dotenv` lines),
  `apply_overlay(env, manifest, ctx, weakenings) -> list[env script]`
  (env files, `env`, paths, each after its defaults, text values substituted as
  they are set: a `dotenv` file line by line, one mapping's entries against the
  environment from before it, a secret-sourced variable never read and marked in
  `env.secret`; the default env script then
  the declared ones, `script-not-run` for a default present that cannot run;
  raises `Refused`), `read_env_script_output(data, *, max_bytes) -> dict` (one
  JSON object, even over several lines, else one per non-blank line), `plan(manifest,
  ctx, *, pinned, enforces_network=False, scripts=None) -> dict` (`supported`,
  `setup`, `python`, `node`, `dependencies`, `needs`, `ungranted`, `weakenings`:
  `deps-network` per dependency file, `unlocked-dependencies` per one that is not
  a lock; raises `Refused` for `partial-lock`), `class
  Activation`, `effective(manifest, ctx, *, base_env, pinned,
  enforces_network=False) -> Activation` (the state variable from `ctx.state`),
  `insecure_url(url)`.
- Dependencies: `requirements_locked(text, path) -> bool` (every requirement
  hashed; `Refused("partial-lock")` when only some are), `environment_name(variant)`
  (`shared`, `isolated` or a normalized name), `dependencies_of(manifest, files,
  os_name, read=None) -> list[{"runtime", "file", "lock", "environment"}]` (a
  selected variant naming `requirements`, `lock` or `package` replaces the default
  file; `[defaults] deps = false` turns the defaults off), `overlay_paths(manifest,
  ctx)` (the default additions, each runtime's `ctx.packages` directory, then
  `paths`), `environment_key(dependency, identity) -> (runtime, environment,
  owner)`, `asked_versions(runtime, text) -> dict` (an `==` pin per normalized
  Python name, or a `package.json`'s ranges), `environments(members, removed=())
  -> {"environments", "conflicts", "blocked", "left"}` (members of one
  environment asking for one package at two versions are blocked; a Python
  environment is locked when every file is, a Node.js one with one locked member).
- Preparation: `SETUP_CHANGES = ("add", "sync", "activate", "remove")`,
  `SETUP_PHASES = ("install", "sync", "remove")`, `setup_phase(change, recorded,
  digest) -> Optional[str]` (add and sync: `install` with no record, `sync` with
  any; activate: only for another digest; remove: `remove` after a record),
  `preparation(change, record, digest, setup, dependencies, fails=None) -> dict`
  (`setup_phase`, `setup_arguments`, `steps` — `empty-state`, `deps:<runtime>`,
  `setup`, `record` or `delete` —, the `record` after them and whether the digest
  `activates`; a failed sync keeps only the recorded digest active).
- Resolution: `class ResolveError(Exception)` (`chain`), `resolve(requires_of,
  requested) -> (visiting order, missing)`, `order_key(priority, name, identity)`.
- Hooks (the hook vectors in `tests/aos_vectors/hooks*.json` pass): `HOOK_EVENTS`
  (event → class, runs `hook.<ext>` by default, outputs, refusal, what `match` selects),
  `SESSION_REASONS`, `TOOL_KINDS`; `[hooks]` and `[defaults] hooks` validate in
  `validate`. `default_hook(files, os_name, interpreters)`, `hooks_of(manifest, files,
  os_name, interpreters) -> (entries, script-not-run files)`, `hook_plan(manifest, facts)
  -> {hooks, on, activates, needs, reports, non_defaults}` (`on` only while the digest is
  the one allowed; `activates` false for a guard that is off), `hook_matches(event, match,
  value)`, `hook_payload(event, known)` with `PAYLOAD_COMMON`, `PAYLOAD_KEYS`, `TOOL_KEYS`;
  `read_hook_output(event, data, sets)` / `hook_result(event, run, limits)` raise
  `HookFailure` (`.cause`: `not-run`, `payload-too-large`, `timeout`, `too-large`, `exit`,
  `grant`, `output`) with `HOOK_OUTPUT_KEYS`, `MAX_HOOK_REASON`; `fire_event(event, runs,
  applies, limits, windows) -> dict` combines hooks to the tightest (a run holding `start`, a
  function, is started only when its turn comes); `apply_pre_env(env, identities, runs,
  limits, reports, non_defaults)` applies `pre-env` output (raises `Refused("hook-refused")`);
  `DEFAULT_HOOK_TIMEOUT = 30`, `DEFAULT_HOOK_LIMITS` (60 s a run, 120 s an event, 64 KiB
  output and error, 1 MiB payload). `tree_entries(root, *, with_data=False)` hands back each
  file's bytes (read once, at most `MAX_TREE_BYTES = 256 MiB`) instead of its digest, so a digest
  and a copy can be made from the same bytes.

## `dotagents._activate`

Which installed overlays are active, and how each is applied.

- `refusal(overlay, *, prepared=True) -> Optional[str]` — why an overlay may not
  be activated: a store setting that is invalid; pinned, a tree with no digest, a
  digest that is not its pin, an excluded entry (a `__pycache__`, a `.pyc`) in its
  copy; a requirements file hashed in part; and, with `prepared`, for an overlay
  with something to prepare, a digest other than the one its last preparation
  succeeded for here. dotagents does not prepare at activation: it refuses, and
  `overlays sync` prepares.
- `needs_preparation(overlay) -> bool` — a dependency file or a setup on this OS.
  `preparing(path)` — a context manager: the overlay at `path` counts as prepared
  while it is being installed and prepared.
- `active(overlays, logger=None) -> list` — the ones not refused, less those whose
  dependencies conflict in one environment (`_deps.conflicts`); each refusal
  logged once per process. `Scope.overlays` is this over `Scope.installed_overlays`.
- `digest_of(overlay) -> (digest, why)` — memoized per process on the install
  record and manifest. `is_pinned(overlay) -> bool`.
- `state_dir(overlay) -> Optional[Path]` — for an overlay with a setup or an env
  script, declared or default: `<store>/overlays/.dotagents-state/<name>/<os>/`,
  kept across syncs until the overlay is removed.
- `files(overlay) -> list[str]` — its member paths (`_aos.member_paths`).
  `interpreters() -> set[str]` — the AOS interpreters this machine can run, once
  per process.
- `context(overlay, *, host_env=None) -> _aos.Context` — its root, env files read
  from inside it, its store's grants and secrets (`env` provider: `host_env`,
  default `os.environ`), its env-script opt-in, its default paths, its state
  directory and its environments' packages directories (`_deps.packages_for`).
- `describe(weakenings)`, `report(logger, weakenings)` — one warning line,
  `overlay[id subject, ...]; ...`.

## `dotagents._policy`

The consumer's settings for overlays, in each store's registry document
(`<store>/dotagents.{toml,json,yaml,yml}`, the `overlays` form only): an overlay
uses the settings of the store it is installed in. Nothing is granted and no env
script runs by default.

```toml
secrets_dir = "~/.agent-secrets"   # the `file` provider's directory
env_scripts = false                # the store's default
[overlays.docs]
source = "<repo>@<commit>#overlays/docs"   # optional: a table without one offers nothing
pin = "sha256:<64 hex>"
env_scripts = true
grants = { env = ["DOCS_TOKEN"], secret = ["signing"], file = ["docs/token"] }
[secrets.signing]
provider = "env"
key = "RELEASE_SIGNING_KEY"
```

- `class PolicyError(SystemExit)`. `class StorePolicy(store)` — `pin(identity)`,
  `env_scripts(identity)`, `grants(identity)`, `named_secret(name, host_env)`,
  `read_secret_file(key)`, `secrets_for(identity, host_env=None) -> _aos.Secrets`,
  `entry(identity)`. `for_store(store)`, `for_overlay(overlay)` — memoized on the
  registry file.

## `dotagents._run`

How setup and env scripts run: their interpreter found on the base `PATH`
(`python` is the scope venv's; `pwsh -NoLogo -NoProfile -NonInteractive -File` for
`powershell`; Git's bash and sh on Windows; never a `.cmd` / `.bat`), arguments as
a list, a timeout, and a process group (POSIX) or job object (Windows, the process
started suspended until it is in the job) so nothing it started outlives it.

- `TIMED_OUT = 124`. `class InterpreterError(Exception)`.
  `interpreter_command(name, *, python, base_path) -> list[str]`, `can_run(name) -> bool`.
  `version_of(name, command) -> Optional[str]` (python, node).
  `run_step(argv, *, cwd, env, timeout, capture=False, max_output=1 MiB) ->
  (status, stdout)`. `pycache_prefix(user_store) -> str` — where Python run for an
  overlay keeps bytecode (`<user store>/.cache/pycache`), never inside an overlay.
  `run_hook(argv, *, cwd, env, timeout, stdin, max_output, max_stderr) -> dict` runs a hook
  bounded: `stdin` written without waiting on the child, stdout kept to `max_output` (a run
  that writes more is stopped: `too_large`), stderr kept to `max_stderr` and the rest read and
  dropped, ended at `timeout` (`timed_out`), every process it started ended with it; returns
  `exit`, `stdout`, `stderr`, `timed_out`, `too_large`, `not_run`, `seconds`.

## `dotagents._resources`

The package's own data; library code, importing nothing from `dotagents.cli`.

- `BASE_ROOT: Path` — the bundled base overlay dir (`_overlay/`), extracted to a
  per-process scratch dir when running from a `.pyz`.
- `BASE_AGENTS_TEMPLATE = "dotagents/templates/AGENTS.md"`,
  `BASE_PROJECT_TEMPLATE = "dotagents/templates/PROJECT.md"`,
  `AGENTS_MD_PLACEHOLDER = "{{AGENTS_MD}}"`.
- `base_agents_text(src, dest, *, project=False) -> str` — the base block for the
  store at `dest`: the project template for a project store when the base has one,
  else the user template, else `<src>/AGENTS.md`; `{{AGENTS_MD}}` rendered as
  `home_relative(<dest>/AGENTS.md)`, so a store synced between machines whose home
  directories differ renders the same block.
- `home_relative(path) -> str` — `path` as POSIX text, `~/<rest>` when it is under
  `Path.home()`, absolute otherwise.
- `pyz_archive() -> str | None` — the `.pyz` this dotagents is imported from (the
  ancestor of the module's `__file__` that is a file), or `None` for a plain
  install. The archive also holds the vendored duho and pathapi; `env`
  exports it as `AGENTS_PYLIB`.

## Commands — `dotagents.cli.init`, `dotagents.cli.overlays`, `dotagents.cli.context`, `dotagents.cli.env`, `dotagents.cli.path`, `dotagents.cli.about`, `dotagents.cli.build_pyz`, `dotagents.cli.mcp`, `dotagents.cli.hook`

One `duho` command class per command above; fields are the flags, `__call__()`
returns the exit code.

- `init.Init`; `overlays.Overlays` (umbrella) with `OverlayAdd`, `OverlayRemove`,
  `OverlayList`, `OverlaySync`, `OverlayShow`, `OverlayHooks`, `OverlayAllowHooks`,
  `OverlayRevokeHooks`; `hook.Hook`; `context.Context` (`FORMATS =
  ("markdown", "system-reminder", "json")`); `env.Env`; `path.PathCmd`
  (`PATH_FORMATS = ("list", "native", "posix", "json")`); `build_pyz.BuildPyz`;
  `about.About`.
- `env`: `write_into(path, text, output_format)` — what `--into` does: the file
  keeps ONE block between `INTO_BEGIN = ": dotagents-env-begin"` and
  `INTO_END = ": dotagents-env-end"` (no-op commands, not comments, so a `; cmd`
  appended to the last line still runs; earlier blocks removed, other lines kept,
  the new block last), written atomically; `INTO_FORMATS = ("export",)`. With
  `--diff --format export`, a `PATH` that is the caller's with entries in front is
  written as `export PATH='<prefix>'"${PATH:+:$PATH}"`.
- `about`: `DISTRIBUTION = "dotagents-cli"`, `BUNDLE_FILE = "_bundle.json"` (written
  by `build-pyz` into the package), `RUNTIME_PACKAGES = ("duho", "pathapi",
  "pathapi-uri", "netimps", "sftppath", "objpath", "repopath")` (the only packages a
  plain install reports);
  `bundle_manifest() -> Optional[dict]`, `installed_packages() -> dict[str, str]`,
  `packages() -> tuple[dict[str, str], Optional[dict]]` (the bundle's packages, else
  the installed ones; the CLI itself in neither).
- `mcp.Mcp` — `prefix: Optional[str]`; exits 2 when the template uses a placeholder
  other than `{name}` or does not format to a tool-name segment.
- `build_pyz`: `EXTRA_REQUIREMENTS` — `{extra: (requirement, ...)}`, what each
  dotagents-cli extra installs (`uri`, `http`, `mcp-http`, `sftp`, `s3`, `repo`),
  kept equal to `pyproject.toml`; `--extras` vendors each requirement at its floor
  and refuses a name not in it.

## `dotagents._mcp_gateway`, `dotagents._mcp_gateway_config`, `dotagents._mcp_gateway_clients`

- `_mcp_gateway.Gateway(notify, load_specs, name_prefix=None)` — the
  `duho.mcp.ToolProvider` the root's `_mcp_tools_` (`cli._mcp_tools`) builds:
  `list_tools()` (`dotagents.mcp.refresh` and the forwarded tools; servers start on
  the first call), `call_tool(name, arguments)` (`None` for a name it does not
  forward), `close()` (every connection). `load_specs()` runs in the
  constructor, so a `GatewayConfigError` ends `duho.mcp.serve` with status 2 before
  the first request. duho's server answers everything else (`-32602` for an unknown
  tool or bad arguments, bad arguments a tool result from protocol `2025-11-25` on).
- `_mcp_gateway_config.discover(scope) -> list` — the validated server declarations
  of the scope's active overlays; raises `GatewayConfigError` (a `ValueError`) on
  the first invalid one, or on a `[[mcp.skills]]` entry without
  `skills/<name>/SKILL.md`.
- `_mcp_gateway_clients` — the stdio and Streamable HTTP clients; private. A
  stdio server's text `env` values are substituted against what it receives (the
  gateway's base variables, its granted `env_passthrough`, which no placeholder
  reads, its root variable, `AOS_OVERLAY_ROOT` and `AOS_OVERLAY_NAME`), the
  entries not seeing each other.

## `dotagents._scope`

- `LEVEL_NAMES` — `default`, `overlay`, `system`, `user`, `project`, `project-root`;
  reserved, never overlay names.
- `class Scope(level, agents_root, *, user_root=None, project_root=None,
  system_root=None)` — `level` `"user"` / `"project"`; `agents_root` the scope's own
  store (the write target). A project scope defaults `user_root` to
  `resolve_user_store()` and `project_root` to `agents_root.parent`; a project whose
  `agents_root` is the user store becomes the user scope (keeping `project_root`).
  `system_root` defaults to `system_root_default()`.
  - `Scope.of(*, agents_dir, project_root=None, global_scope=False) -> Scope` — the
    read walk's scope: user scope when `global_scope` or no `project_root`, else a
    project scope at `<project_root>/.agents`.
  - Attributes: `level`, `agents_root`, `user_root`, `project_root`, `system_root`.
    Properties: `global_scope` (user level), `project_store` (`None` in the user
    scope), `stores` (system, user, project — existing roles only, a directory
    listed once), `installed_overlays` (`Overlay.installed(*stores)`),
    `overlays` (the active ones: `_activate.active(installed_overlays)`), `overlay_root`
    (`<agents_root>/overlays`), `shared_skills_dir` (`<agents_root>/skills`).
  - `store_level(store) -> str` — `"system"` / `"user"` / `"project"`; `ValueError`
    for another path.
  - `overlay_dir(name) -> Path` — `<overlay_root>/<name>`.
  - `paths(*names, include_missing=False) -> list[tuple[str, Path, Optional[Path]]]`
    — the contract-A walk. Each name is a filename (resolved at every level) or a
    dict `{"default": ..., "overlay": ..., "<level>": ...}` where an empty or missing
    entry skips that level. Returns `(level, path, root)`: for an overlay `level` is
    its dir name and `root` its dir; `root is None` for every other level. Existing
    paths only unless `include_missing`.
- `resolve_scope(global_scope=False, *, agents_dir=None, project_root=None) -> Scope`
  — the write scope: `-g` → user scope at `resolve_user_store(agents_dir)`; else a
  project scope at `agents_dir` (if given) or `<root>/.agents`, the root being
  `project_root` or `install_project_root()`.
- `SYSTEM_ROOT_ENV = "AGENTS_SYSTEM_ROOT"`; `system_root_default() -> Optional[Path]`
  — the system store, or `None` (unset on Windows, not absolute, not a directory,
  or writable by a non-administrator — the last two with a warning). Cached per
  process and value.
- `AGENTS_DIR_ENV = "AGENTS_HOME"`; `resolve_user_store(agents_dir=None) -> Path` —
  `agents_dir` → `$AGENTS_HOME` → `~/.agents`.
- `project_root_default() -> Path` — `$AGENTS_PROJECT_ROOT` → `$CLAUDE_PROJECT_DIR`
  → the cwd.
- `install_project_root() -> Path` — the pinned root (same two vars) while the cwd
  is inside it; outside it, the nearest ancestor of the cwd with a `.git` or its own
  `.agents` (never `~` or above, never a store's parent), else the cwd, with a
  warning; no pin: the cwd.
- `filter_names(names, pattern) -> list[str]` — `fnmatch` filter; `None` / `"*"`
  keep all.
- `resolve_source(repos=None, *, scope=None, logger=None, allow_empty=False) ->
  CompositeSource` — `_sources.resolve` over `repos`, the env repos, then the
  project store's and the user store's registries, caching under
  `<user store>/.cache/overlays`. `SourceError` when nothing is configured, unless
  `allow_empty`.

## `dotagents._overlays`

- `DEFAULT_PRIORITY = 500`. `DEFAULT_SETUP_TIMEOUT = 300` — seconds a setup
  variant runs when it declares no `timeout` and `--setup-timeout` is not given;
  `0` means no limit.
- `load_manifest(path) -> (document, problems)` — `overlay.cfg` read
  (`_aos.read_document`) and validated (`_aos.validate`); a refused one is `({},
  [problem, ...])`; memoized on the file's mtime and size.
- `class InstallResult` — what `install_to` did: `written`, `unchanged`,
  `replaced` (edited here, replaced by the source's file), `removed`, `backed_up`,
  `lines`; property `skipped` (= unchanged). Unpacks as `(written, skipped, lines)`.
- `class Overlay(path, store=None)` — one overlay directory; `os.PathLike`; equal
  and hashed by `path`; nothing read at construction.
  - Class data: `NAME_RE`, `RESERVED_NAMES` (Windows device names),
    `MANIFEST_NAME = "overlay.cfg"`, `OLD_MANIFEST_NAME = "overlay.toml"` (never
    read; a warning names it), `RECORDS_DIR = ".dotagents-install"`,
    `STATE_DIR = ".dotagents-state"`, `OLD_INSTALL_RECORD =
    ".dotagents-install.json"` (an install record inside the overlay: read when
    none is beside it, removed by the next install), `DEFAULT_PRIORITY`.
  - `Overlay.is_valid_name(name) -> bool` — `NAME_RE` (a letter, then letters,
    digits, `_`, `.`, `-`, ending in a letter or digit), not a `LEVEL_NAMES` entry,
    not a Windows device name (with or without an extension).
  - `Overlay.normalize_name(name) -> str` — lowercase, `_` and `.` → `-`: the
    canonical name, the install dir name, the name compared everywhere.
  - `Overlay.root_var_for(name) -> str` — `normalize_name(name)` upper-cased, every
    non-alphanumeric → `_`, plus `_OVERLAY_ROOT`.
  - Properties: `name` (dir name), `normalized_name`, `root_var`, `is_valid`,
    `manifest_path`, `priority`, `sort_key` (`(priority, manifest name, dir
    name)`), `install_record_path` (`<store>/overlays/.dotagents-install/<name>.json`);
    attribute `store`.
  - `Overlay.discover(root, store=None)`, `Overlay.installed(*stores)`,
    `Overlay.sort_by_priority(overlays)` — as before: valid-named dirs under
    `overlays/`, a later store's copy shadowing an earlier one, `sort_key` order.
  - `document() -> dict` — the validated manifest (`{}` when absent or refused;
    a refused one is warned about once). `manifest_problems() -> list[str]`.
  - `read_manifest() -> dict` — `name`, `description`, `routing` (trimmed, blanks
    dropped), `rules`, `requires` (normalized, de-duplicated), `priority`, `doc`.
  - `member_paths() -> list[str]` (no hashing). `skills()`, `rules()`,
    `context_files(harness_id=None)` — the field with its defaults
    (`_aos.skills_of`, `rules_of`, `context_of`), then `harness.<id>.context`.
    `harness_hints(harness_id) -> dict`. `setup_variant(os_name=None)` —
    `_aos.setup_of` here: a declared variant, else the default `setup.<ext>`.
  - `declared_scripts(os_name=None) -> {name: (path, interpreter)}` (its `scripts`
    entries for this OS; no file is a command by its name); `scripts() -> {name:
    path}` (the python ones: they get launchers); `unsupported_scripts() ->
    {name: interpreter}` (refused by `overlays add`).
  - `run_setup(variant, *, phase, env, python, base_path, dry_run, logger,
    timeout=None) -> int` — one setup variant through `_run.run_step`: the phase
    as the first argument before the variant's `args` and as `AOS_SETUP_PHASE`,
    cwd the overlay root, `env` as given, `TIMED_OUT` when stopped, `127` for an
    interpreter it cannot run.
  - `members() -> list[str]`, `files() -> list[Path]` — every member (all regular
    files but the excluded names; the manifest included); raise `DigestError` for
    a link or a refused name. `tree_digest() -> str`.
  - `state_dir(os_name=None) -> Path` — the overlay's own, per OS, kept across
    syncs. `preparation_path(os_name=None) -> Path`
    (`.dotagents-state/<name>/<os>.prepared.json`), `read_preparation() ->
    Optional[{"digest", "phase"}]` (the last preparation that succeeded on this
    OS), `write_preparation(digest, phase)`.
  - `read_install_record() -> dict` — `{"source", "commit", "digest", "files":
    {rel: sha256}, "installed"}`; `{}` when absent. `forget()` — delete the record
    and the state directories and preparation records of a removed overlay.
  - `rule_blocks(rel_paths) -> tuple[list[str], list[str]]` — `(blocks, warnings)`:
    each file's leading `- **` bullets up to its next `## ` heading.
  - `install_to(dest_overlay_dir, dry_run, *, backup_root=None, source=None,
    commit=None) -> InstallResult` — make the install hold exactly the members:
    each installed or replaced, everything else removed (a file dotagents did not
    put there backed up first under `backup_root`; excluded entries not backed
    up); writes the install record beside it.
- `OVERLAY_ROOT_NOTE: str` — the line emitted once above overlay routing lines that
  use `$<NAME>_OVERLAY_ROOT`.
- `recompose_overlay_block(agents_md, base_block, overlays, dry_run, logger) -> bool`
  — rebuild the managed block from the pristine `base_block` over `overlays` in
  `sort_key` order; creates the file when absent; content outside the markers kept;
  returns whether it changed (`False` and a warning when the file has no block).

## `dotagents._launchers`

Launchers for overlays' declared python scripts (`Overlay.scripts()`), in `<venv>/overlays/` of a
dotagents venv; `_pyvenv.sync_venvs` brings them up to date on every sync, and
`_env.get_environment` puts that dir first on `PATH` for the venv `AGENTS_PYTHON`
names. A launcher runs `$<NAME>_OVERLAY_ROOT/<script>` under the venv's
interpreter, resolved when it runs (argv[0] the script, its dir first on
`sys.path`, run as `__main__`); with the variable unset or the script missing it
exits 2 naming both. Windows: `<name>.exe`, distlib's `ScriptMaker` launcher (no
command interpreter parses its arguments). POSIX: `<name>`, mode 0755, a `/bin/sh`
script that `exec`s the interpreter.

- `DIR = "overlays"`, `MANIFEST = ".dotagents-launchers.json"` (in that dir:
  `{name: overlay name}` of the launchers dotagents wrote).
- `class LauncherError(SystemExit)` — two overlays with one script name; the
  message names both.
- `launcher_dir(venv) -> Path`. `file_name(name) -> str` (`.exe` added on Windows).
  `is_launcher_dir(path) -> bool` — the shape `<store>/.pyvenv/<triplet>/overlays`,
  no filesystem access.
- `declared(overlays) -> dict[str, tuple[Overlay, str]]` — every script by name;
  raises `LauncherError` on a name two overlays have.
- `render(name, var, script, python) -> bytes` — one launcher's bytes; equal
  inputs give equal bytes (the `.exe`'s archive carries a fixed time).
- `sync(venv, python, overlays, *, dry_run=False, logger=None) -> list[str]` —
  writes each launcher whose bytes differ (atomic replace), deletes those
  `MANIFEST` lists that no overlay has any more (other files there are left
  alone), rewrites `MANIFEST`; returns the names written or deleted. Raises
  `LauncherError` before any write.

## `dotagents._sources`

Spec grammar: `[git+]<location>[@<ref>][#<path>]`. A location ending `.git`, starting
`git@` / `ssh://` / `git://`, or prefixed `git+` is git (`@ref` only for git);
another `scheme://` is a URL; anything else a local path. For a **repo** `#path` names
a directory of overlays or a registry file (default: the root); for a **source**
(registry value) it names the overlay's root dir (default: the repository root).

- `class SourceError(SystemExit)`; `class OverlayNotFound(SourceError)` — raised only
  when every repo loaded and none offers the name.
- `REPO_ENV_DEFAULT = "AGENTS_OVERLAYS_REPO"`, `REPO_ENV_PREFIX =
  "AGENTS_OVERLAYS_REPO_"`, `REGISTRY_FILE_STEM = "dotagents"`,
  `REGISTRY_FILE_SUFFIXES = (".json", ".toml", ".yaml", ".yml")`,
  `NO_SOURCE_MESSAGE`.
- `class Spec(kind, location, ref=None, path=None)` — `kind` `"git"` / `"url"` /
  `"dir"`; `display() -> str` (credentials removed).
- `parse_spec(text) -> Spec` — `ValueError` when empty; `SourceError` when a git
  location, ref or host starts with `-`, a location holds `::`, a git URL's scheme
  is not `https`/`http`/`ssh`/`git`/`file`, or `#path` is not a relative path.
- `redact(text) -> str` — URL userinfo removed, query values masked.
- `uri_path_class()` — `uripath.URIPath` (from `pathapi-uri`), or `None` without the
  `uri` extra.
- `url_scheme(location) -> str` — the lower-cased scheme.
- `GIT_SSL_REVOKE_KEY = "git_ssl_revoke"`, `GIT_SSL_REVOKE_VALUES = ("best-effort",
  "true", "false")`, `GIT_SSL_REVOKE_DEFAULT = "best-effort"`;
  `git_ssl_revoke(*stores) -> str` — the first store config (`config.toml`) that sets
  the key, project store first; another value raises `SourceError` naming the file.
- `class SourceCache(root, logger=None, ssl_revoke="best-effort")` — every git call
  runs as `git -c http.schannelCheckRevoke=<ssl_revoke> ...` (Git for Windows'
  Schannel checks revocation; git with OpenSSL ignores it), and a failure that looks
  like a revocation check names the key. A git that rejects the value as a bad
  boolean (older than `best-effort`) is asked again without it. Every call also
  carries `-c core.autocrlf=false -c core.symlinks=false`, and each checkout's
  `.git/info/attributes` is `* -text -filter -ident -working-tree-encoding`, so
  its files are the blobs' bytes. `special_entries(checkout, subpath) -> list[str]`
  (symbolic links and submodules of the commit), `commit(checkout)`. `repo_dir(location, ref=None) -> Path`
  (`<root>/<slug>-<hash>`); `checkout(spec) -> Path` (clone or fetch, once per
  process; a failed fetch of an existing checkout is a warning);
  `materialize(spec) -> Path` (a `url` spec: `file://` in place, anything else
  synced into `<root>/uri/` once per process; needs the `uri` extra). Creates a
  `.gitignore` of `*` in `root`.
- `parse_document(text, suffix, origin) -> dict[str, str]` — a registry's
  name → spec map (the document or its `overlays` table, whose entries may be
  tables with a `source`; a table without one offers nothing); TOML needs Python
  3.11+ or `tomli`, YAML `pyyaml`. `load_document(path) -> dict` — a local
  registry file's whole document (`_policy` reads its settings from it).
- `class DirRepo(root, origin=None)` — `available() -> list[str]`, `has(name) ->
  bool`, `overlay_dir(name) -> Path` (literal, normalized, or any dir whose
  normalized name matches; `OverlayNotFound` otherwise).
- `class RegistryRepo(origin, entries, cache, base=None)` — `root` (= origin),
  `key_for(name) -> Optional[str]`, `available()`, `has(name)`, `overlay_dir(name)`
  (an entry that is a directory of overlays yields the one called `name`).
- `is_relative(spec) -> bool`; `resolve_relative(spec, base, *, origin, key) -> Spec`
  — a relative entry resolved beside the registry (same repo and ref inside git,
  the URL beside it for a URL registry).
- `locate(spec, cache) -> Path` — the local path a spec names; `SourceError` when
  missing.
- `load_repo(spec_text, cache)` — `DirRepo` for a directory, `RegistryRepo` for a
  file (or an http(s) registry fetched with the standard library when the `uri`
  extra is absent).
- `source_record(spec) -> dict` — `{kind, location, ref, path, lossy}` for an
  install record: a local location made absolute, credentials removed, `lossy`
  when removal dropped something. `spec_from_record(record) -> Optional[Spec]`;
  `record_display(record) -> str`.
- `registry_files(*stores) -> list[Path]` — the first existing
  `<store>/dotagents.<suffix>` per store.
- `env_repos(environ=None) -> list[str]` — `$AGENTS_OVERLAYS_REPO_<KEY>` values by
  sorted KEY, then `$AGENTS_OVERLAYS_REPO`.
- `class CompositeSource(specs, cache)` — repos in order, loaded lazily: `root`
  (display string), `repos()`, `available(on_error=None) -> list[str]` (with
  `on_error(spec, exc)` a broken repo is reported, not raised), `overlay_dir(name)
  -> Path`, `locate(name) -> tuple[Path, spec]` (first repo offering it), `only(spec)
  -> CompositeSource` (same cache), `find_repo(record) -> Optional[spec]` (the spec
  in the chain an install record describes).
- `resolve(specs, *, cache_root, stores, environ=None, logger=None,
  allow_empty=False) -> CompositeSource` — `specs`, then `env_repos`, then
  `registry_files(*stores)`; `SourceError(NO_SOURCE_MESSAGE)` when empty unless
  `allow_empty`.

## `dotagents._env`

Assembly order (`get_environment`): identity (`stamp_identity`), then the roots
and every active overlay's root and state variables, then the overlays'
declarations in composition order (`_apply_declarations`: env files, `env`,
default paths then `paths`, env scripts when enabled; an overlay that cannot be
applied contributes nothing, with an error naming why; weakenings reported in
one warning), then the stores' `bin` on `PATH`, then `PYTHONPATH` /
`AGENTS_PYTHONPATH`, then the stores' env files, then the proxy model. `PATH`:
every store's `bin` prepended, then, first of all, the active
venv's launcher dir (`<venv>/overlays`, `_launchers`) when `AGENTS_PYTHON` is that
venv's interpreter and the dir exists; every other dotagents venv's launcher dir is
taken out. First even before the `bin` dirs: cmd.exe and PowerShell try every
`PATHEXT` extension in a directory before the next one, so a `bin/<name>.py` (with
`.PY` in `PATHEXT`) or a `.cmd` there would otherwise win.

- Roots: `AGENTS_HOME` and `AGENTS_PROJECT_ROOT` name the stores walked, absolute —
  an inherited value naming the same directory holds, another is replaced; in a
  user-scope walk `AGENTS_PROJECT_ROOT` is set only when unset. `AGENTS_PYTHON`:
  the active scope venv's interpreter (`_pyvenv.active_venv`), else
  `sys.executable`; it replaces an inherited value equal to `sys.executable` or
  naming another `.pyvenv` interpreter, and any other pin holds. `AGENTS_PYVENV`:
  that venv's dir, removed when there is none. Always: one `<NAME>_OVERLAY_ROOT`
  per `scope.overlays`, the copy in play whatever was inherited, and
  `<NAME>_OVERLAY_STATE` for one with a state directory. Only when unset:
  `PYTHONPYCACHEPREFIX` (`_run.pycache_prefix`). `AGENTS_PYLIB` (`_env.PYLIB_VAR`): the `.pyz` when
  dotagents runs from one (`_resources.pyz_archive()`), else not set — where its
  own libraries (duho, pathapi) import from by zipimport for an overlay
  built on them. Never added to `PYTHONPATH`: a consumer appends it to
  `sys.path` when its import fails.
- `PATH`: an overlay's additions as AOS defines them (its prepends first, empty
  components dropped, an entry listed twice once); then every store's `bin`
  (not project-root, missing dirs included) at the front, whatever the caller's
  `PATH` held; empty and cwd-relative inherited entries are dropped.
- `PYTHONPATH`: an overlay's additions (its `lib/python/` prepended by
  default); every existing store `lib` the active venv's `.pth` does not already
  layer, at the front, highest precedence first; the entries an inherited
  `AGENTS_PYTHONPATH` named (another scope's libs) are removed first.
  `AGENTS_PYTHONPATH`: every agent library dir (`get_lib_paths`),
  `os.pathsep`-joined; removed when there are none.
- Env files: the stores' only -- all `pre.env.py` / `pre.env` (+ the project
  store's `pre.local.env`), then all `env.py` / `env` (+ `local.env`), store order,
  regular files only, each evaluated against everything before it; the stores'
  `bin` / `lib` are on `PATH` / `PYTHONPATH` for both tiers. An overlay's
  files with these names mean nothing. The project root contributes none. A layer
  that raises is skipped with a warning.
- Proxy: `AGENTS_PROXY` seeded when unset from `AGENTS_WEBFETCH_PROXY_URL`, then
  `HTTPS_PROXY` / `HTTP_PROXY` / `ALL_PROXY` (either case); each set proxy variable
  mirrored into its other case.

API:

- `class EnvChanges(dict)` — vars to set, plus `removed: set[str]` (vars to unset;
  never also a key).
- `get_environment(scope, *, base_env=None, explicit=None, logger=None) ->
  EnvChanges` — the changes against `base_env` (default `os.environ`); `explicit`
  names the agent for the identity, overriding inherited identity vars.
- `get_diff(scope, *, base_env=None, explicit=None, logger=None) -> EnvChanges` —
  only the values that differ from `base_env`, plus removals of vars it has.
- `get_env_from_py(env_py, base_env, *, level="", global_scope=False, logger=None)
  -> EnvChanges` — runs `<interpreter(base_env)> env.py --level <level> --agent
  <level> [--global]` with `base_env` (plus OS bootstrap vars and
  `PYTHONIOENCODING=utf-8`), `AGENTS_PYTHONPATH` kept at the front of its `PYTHONPATH`, in
  the caller's cwd. Stdout: one JSON object, or one per line merged in order.
  Values must be strings; `null` → `removed`; other types, invalid keys (empty,
  `=`, NUL, non-UTF-8) and values with NUL or non-UTF-8 bytes are skipped with a
  warning naming the key. Non-zero exit or unparseable output → empty, with a
  warning; values are never logged.
- `get_env_from_file(env_file, base_env, logger=None) -> EnvChanges` — sources the
  file in bash (`set -a`, output discarded) and returns what changed, `unset`
  included, from before/after snapshots taken in one bash process (MSYS2 `*PATH`
  values converted back to Windows form). No bash, a failed `source` or an `exit`
  in the file → empty, with a warning.
- `find_bash(logger=None) -> Optional[str]` — the bash for plain env files, resolved
  once per process: `bash` on `PATH` on POSIX; on Windows Git's bash (beside `git`
  on `PATH`, then the standard install dirs, then `PATH`), accepted only if it is
  MSYS2 / Cygwin (never the WSL launcher). Warns once when there is none.
- `interpreter(osenv) -> str` — `osenv["AGENTS_PYTHON"]` when it names a file, else
  `sys.executable`.
- `get_bin_paths(scope)` (each store's `bin`), `get_path_dirs(scope)` (overlays'
  `PATH` additions in composition order, then the stores' `bin`: lowest precedence
  first, the order `get_environment` applies them), `get_store_lib_paths(scope)`,
  `get_lib_paths(scope)` (overlays' `PYTHONPATH` additions, then the stores' `lib`,
  same order, existing dirs only), `declared_paths(overlay, variable) -> list[Path]` (default
  then declared), `get_overlay_roots(scope)` (the dirs of `scope.overlays`),
  `resolve_env_files(scope) -> list[tuple[str, Path, Optional[Path]]]` (pre tier
  then main tier, stores only, as `Scope.paths` tuples).
- `overlay_process_env(scope, overlay, *, base_env=None, logger=None) ->
  Optional[dict]` — the environment of a process one overlay runs (its setup): the
  base, `AGENTS_HOME` / `AGENTS_PYTHON` / `PYTHONPYCACHEPREFIX`, every active
  overlay's root and state variable, the path additions of the overlays it
  requires, its own declarations, `AOS_OVERLAY_ROOT` / `_NAME` / `_STATE`; None
  when its declarations cannot be applied.
- `apply_proxy_model(osenv) -> dict[str, str]` — the proxy changes described above.
- `ENV_CACHE_TTL = 300.0` (seconds). `env_cache_key(scope, base_env, *parts) -> str`
  — a digest of what an assembly reads without running it: roots, cwd, interpreter,
  package version, installed overlays, each env-file candidate's
  existence/mtime/size, every file under an existing `lib`, each store's venv state
  file, the stores' registry files, each overlay's manifest, install record,
  `bin` / `lib` and the env files it names, the whole `base_env`,
  and `parts` (format, mode). `read_env_cache(scope, key, *, now=None) ->
  Optional[str]` (None when absent or older than the TTL); `write_env_cache(scope,
  key, text) -> None` (owner-only, under `<user store>/.cache/env/`; never raises).
- `FORMAT_ALIASES: dict[str, str]` (alias → canonical format), `KNOWN_FORMATS`
  (every accepted `--format`, `auto` included), `detect_shell_format() -> str`
  (from the parent-process chain; `"powershell"` on Windows / `"export"` elsewhere
  when unknown; never raises).

## `dotagents._context`

- `assemble_context(agent, scope, *, inline=False, expand_vars=True) -> str` — the
  context markdown, or `""` when nothing is left after subtracting
  `agent.loaded_paths(project_root)`. Sources in order: contract-A `AGENTS.md`
  (every store and the project root) and `AGENTS.local.md` (project store and
  project root), then each active overlay's `context_files(agent.harness_id)` by
  `Overlay.sort_key` (front matter dropped), each prefixed
  `<!-- Source: <path> -->`. Then a skills listing (`- **name** (`<SKILL.md>`):
  description`, from each skill's leading frontmatter; a project skill wins), then
  a "Local additions to overlay files" section when `local_additions` finds any,
  then, with `expand_vars`, the line ``Python for skills (`$AGENTS_PYTHON`): `<path>` ``
  (`skills_python`).
  `<PROJECT_ROOT>` and `<NAME_OVERLAY_ROOT>` placeholders expand; with
  `expand_vars` so do `$NAME_OVERLAY_ROOT` / `${NAME_OVERLAY_ROOT}`. `inline`
  appends the on-demand `.md` files the sources reference, each resolved against
  its own source's directory (a project's also against its `.agents`; a user or
  system source never into the project).
- `assemble_context_data(agent, scope, *, inline=False, expand_vars=True) -> dict`
  — `{"agent", "harness", "sources", "context", "skills": [{"name",
  "description", "path"}], "local_additions": [{"overlay_file", "local": [...]}],
  "python"}`; `context` without the skills and local-additions listings.
- `skills_python(scope) -> str` — the active scope venv's interpreter, else
  `sys.executable`.
- `local_additions(scope) -> list[tuple[Path, list[Path]]]` — the user's own
  additions to installed overlay files: for each `.md` an overlay in `scope.overlays`
  ships in a subdirectory other than `skills/`, every store's file at the same
  relative path (`<store>/kb/RUST.md` for the overlay's `kb/RUST.md`), in
  `scope.stores` order. A store's root files never match.

## `dotagents._agents`

- `class Agent` — the adapter base. Class data: `name` (registry name),
  `harness_id` (e.g. `claude-code`), `vendor`, `model_source_vars`,
  `detect_env_vars`, `context_files` (config-file detection), `harness_loads`
  (paths the harness loads itself: `~/` or `/` absolute, else under the project
  root), `context_target` (the file `write_context` merges into, relative to the
  project root; `""` = none), `launch_command` (the CLI program; `""` = none),
  `include_entry` (`(user path under ~, project path)` of the entry file that gets
  the store's include; `()` = none), `base_config_note` (what `init` logs for a
  harness it links nothing into), `scope_level` / `project_root` (set by `init`).
  - `detect_env(environ) -> bool` — any `detect_env_vars` present.
  - `detect(root) -> bool` — any `context_files` exists under `root`.
  - `resolve_model(environ) -> Optional[str]` — first set `model_source_vars` value.
  - `write_base_config(dest, *, dry_run, logger) -> None` — the harness's link to
    `<dest>/AGENTS.md` (the store file itself is written by `init`): an
    `@<dest>/AGENTS.md` line appended as a managed block to the `include_entry`
    file (skipped when the line is already there; a warning when a project's file
    is not gitignored), else a `base_config_note` log line.
  - `write_context(project_root, effective_context, *, dry_run, logger) -> None` —
    merge a `dotagents:context` block into `<project_root>/<context_target>`.
  - `loaded_paths(project_root) -> list[Path]` — resolved `harness_loads`.
  - `wire_hooks(dest, *, dry_run, logger, config_root=None) -> None` — merge the
    harness's hooks; `config_root` redirects its config dir. Base: nothing.
  - `skills_wired(dest) -> bool` — the harness's config already carries dotagents'
    wiring for the store `dest`, so `overlays add` / `sync` / `remove` keep its
    skills current without `init`. Base: `False`.
  - `link_skills(dest, *, dry_run, logger, config_root=None) -> None` — link
    `<dest>/skills/<name>` into the harness's own skills dir and prune what a
    removed skill left. Base: nothing; `ClaudeAgent` uses
    `_skills.link_skills_into`.
  - `launch_context_args(context_file) -> Optional[list[str]]` — argv appending the
    context to the system prompt, or `None` (then `launch` uses `write_context`).
- Adapters (`name`: harness id, `context_target`, `launch_command`, markers):
  - `ClaudeAgent` (`claude`: `claude-code`, `.claude/CLAUDE.md`, `claude`,
    `CLAUDECODE` / `CLAUDE_CODE_ENTRYPOINT`) — include `@<store>/AGENTS.md` (relative
    when the store is beside the config dir) in `<$CLAUDE_CONFIG_DIR|~/.claude>/CLAUDE.md`
    (user) or `<project>/.claude/CLAUDE.md`; `loaded_paths` follows the entry files'
    `@` imports (up to 4 hops; code spans and fences skipped) in the user file and in
    the project root's and every ancestor's `CLAUDE.md` / `CLAUDE.local.md` /
    `.claude/CLAUDE.md`, plus each `AGENTS.md` Claude reads as a fallback;
    `wire_hooks` links each `<store>/skills/<name>` into `<config>/skills/<name>`
    and merges `SessionStart` + `CwdChanged` (bash; plus `shell: "powershell"`
    variants on Windows that run only without Git Bash, removed elsewhere) into
    `settings.json` (user) or `<project>/.claude/settings.local.json`, and with
    `powershell_env_hook = True` (Windows) a `PreToolUse` hook matched on
    `PowerShell` (`PRETOOLUSE_STATUS`). Command constants: `SESSION_START_COMMAND`,
    `CWD_CHANGED_COMMAND`, `SESSION_START_COMMAND_POWERSHELL`,
    `CWD_CHANGED_COMMAND_POWERSHELL`, `PRETOOLUSE_POWERSHELL_COMMAND`;
    `ENTRY_FILES`. `launch_context_args` → `--append-system-prompt-file <file>`.
  - `CodexAgent` (`codex`: `codex`, `AGENTS.md`, `codex`, any `CODEX_SANDBOX*`) —
    user scope only: deploys `SESSION_START_HOOK_SCRIPT` and
    `PRETOOLUSE_HOOK_SCRIPT` into `<$CODEX_HOME|~/.codex>/hooks/`, merges
    `SessionStart` and `PreToolUse` (matcher `Bash`) into `hooks.json`, and
    `remove_env_block(*, dry_run, logger, config_root=None)` deletes the old
    `ENV_BLOCK_BEGIN` / `ENV_BLOCK_END` block from `config.toml`.
    `CodexAgent.hook_commands(root, script_name) -> tuple[str, str]` —
    `(command, commandWindows)` running the script under `sys.executable`.
  - `AntigravityAgent` (`antigravity`: `antigravity`, `.agents/rules/dotagents.md`,
    no CLI, never detected) — user scope only: deploys
    `PREINVOCATION_HOOK_SCRIPT` into `~/.gemini/config/hooks/` and merges a
    `PreInvocation` hook under the `"dotagents"` key of `~/.gemini/config/hooks.json`.
  - `GeminiAgent` (`gemini`: `gemini-cli`, `GEMINI.md`, `gemini`, `GEMINI_CLI`) —
    `@<store>/AGENTS.md` import in `~/.gemini/GEMINI.md` (user) or
    `<project>/GEMINI.md`; no hooks.
  - `CursorAgent` (`cursor`: `cursor`, `.cursor/rules/dotagents.mdc` created with
    `alwaysApply: true`, `cursor-agent`, `CURSOR_AGENT`), `CopilotAgent`
    (`copilot`: `copilot`, `.github/copilot-instructions.md`, `copilot`, no marker)
    — no include, no hooks.
  - `PiAgent` (`pi`: `pi`, `.pi/APPEND_SYSTEM.md`, `pi`, `PI_CODING_AGENT`) — user
    scope: a managed block pointing at the store's `AGENTS.md` in
    `<$PI_CODING_AGENT_DIR|~/.pi/agent>/AGENTS.md`; project scope: a
    `POINTER_BEGIN_MARKER` / `POINTER_END_MARKER` block pointing at
    `.agents/AGENTS.md` in `<project>/.pi/APPEND_SYSTEM.md` (warned when git
    does not ignore it); `launch_context_args` →
    `--append-system-prompt <text>` on POSIX, `None` on Windows.
- `get_agent(name) -> Optional[Agent]` — a fresh adapter by registry name.
- `get_all_agents() -> list[Agent]` — one of each, registry order (claude, gemini,
  antigravity, codex, cursor, copilot, pi).
- `detect_runtime_agent(environ, explicit=None) -> Optional[Agent]` — `explicit`
  (registry name) > `$AGENTS_HARNESS` (registry name or harness id) > markers;
  `None` in a plain shell.
- `resolve_active_agent(environ, explicit=None, root=None) -> Agent` —
  `detect_runtime_agent`, else config-file `detect(root or cwd)`, else
  `ClaudeAgent()`.
- `stamp_identity(environ, explicit=None, root=None) -> dict[str, str]` —
  `AGENTS_HARNESS`, `AGENTS_VENDOR`, `AGENT` (= harness id) and `AGENTS_MODEL` when
  derivable, for the runtime agent only (`{}` in a plain shell); never replaces a
  value in `environ` unless `explicit` names a known agent. `root` is unused.

- `Agent.wire_overlay_hooks(dest, *, dry_run, logger, config_root=None)` — wire the
  harness's own hooks to `dotagents hook` for the events the overlays installed in the
  store `dest` have a hook for, and retract an entry no overlay uses any more; a no-op on
  the base class. `ClaudeAgent` writes `settings.json` (user) or `settings.local.json`
  (project) with a bash entry per event and, on Windows, a PowerShell one that selects itself
  when there is no Git Bash; `CodexAgent` writes the user `hooks.json` (with
  `commandWindows`). `init -g` and `overlays add/sync/remove` call it; the entries are
  labelled `dotagents: Overlay hooks: <native event>` and run `overlay_hook.py`.

## `dotagents._merge`

Marker lines (a marker counts only on a line of its own, outside fenced code):

- `BEGIN_MARKER = "<!-- dotagents:begin -->"`, `END_MARKER = "<!-- dotagents:end -->"`,
  `CONTEXT_BEGIN_MARKER = "<!-- dotagents:context:begin -->"`,
  `CONTEXT_END_MARKER = "<!-- dotagents:context:end -->"`.
- `find_block(text, begin_marker=BEGIN_MARKER, end_marker=END_MARKER) ->
  Optional[tuple[int, int]]` — the block's span, markers included. For any marker
  pair but the context one, marker lines inside a `dotagents:context` block are
  not the file's block.
- `merge_block(target, block_source_text, *, force=False, dry_run=False,
  backup_root=None, begin_marker=..., end_marker=..., append=False) -> str` — returns
  `"created"`, `"block-inserted"` (prepended, or appended with `append`),
  `"block-refreshed"`, `"skipped (present)"` (block already current), or with
  `force` `"unchanged"`, `"replaced (--force, backed up)"` or
  `"replaced (--force)"`. Content outside the markers is kept unless `force`
  (whole file replaced; the original copied under `backup_root`, never over an
  earlier backup, `external/…` for a file outside the store). A begin marker with
  no end is refused (`SystemExit`). A UTF-8 BOM is read through and not written.
  Every write is atomic and goes through a symlink to its target.
- `remove_block(target, *, dry_run=False, begin_marker=..., end_marker=...) -> str`
  — `"removed"` or `"absent"`.
- `merge_include_line(target, include_line, *, force=False, dry_run=False,
  backup_root=None) -> str` — an `@path` line in a managed block appended to a
  harness entry file; `"skipped (present)"` when the line is already anywhere in it.
- `merge_context_block(target, context_text, *, dry_run=False) -> str` — the
  `dotagents:context` block, appended after existing content; marker lines inside
  `context_text` are dropped.
- `timestamped_backup_root(dest) -> Path` — `<dest>/install_backup/<timestamp>`, one
  per store per process.

## `dotagents._overlay_hooks`

Firing overlay hooks (the model is `dotagents._aos`; the user-facing page is
`docs/guide/overlay-hooks.md`). A hook is the overlay's code: it runs only while its
overlay's tree digest is the one the user allowed, as an argv list with the overlay root as
its directory, bounded by `LIMITS`.

- `LIMITS` (`DEFAULT_HOOK_LIMITS`, a copy tests may change), `NO_HARNESS = "dotagents"`,
  `class Harness` (`id`, `native`: AOS event → native name, `applies`: event → outputs
  dotagents can apply, `reasons`, `fires`), `CLAUDE`, `CODEX`, `ANYWHERE` (no
  harness known: nothing reported as unfireable), `HARNESSES`, `harness_of(ident) -> Harness`
  (an unknown id fires only `pre-env`), `partial_events(harness)`.
- Settings, read from the user's own store only (never an overlay or a project):
  `SETTING_BOUNDS` (`timeout`, `event_timeout`, `deadline`, `max_output`, `max_payload`, each
  with its bounds), `settings_path(user_store)` (`overlays/.dotagents-state/hooks-settings.json`),
  `read_settings`, `limits_for(user_store)` (`LIMITS` under them), `deadline_for` (default
  `DEFAULT_DEADLINE = 150` s for everything a hook entry does, from the process's start),
  `entry_timeout(user_store)` (the harness's own timeout for the entry: deadline + 20 s, at
  most `ENTRY_TIMEOUT_CAP = 600`).
- Allowances, in the user store only, keyed by the overlay's store and name:
  `allowances_path(user_store)`, `read_allowances`, `allowed_digest(user_store, identity,
  store=None)`, `allow(user_store, identity, digest, store=None)`, `revoke(...) -> bool`.
- `install_notice(overlay, user_store) -> Optional[str]` — the line `overlays add/sync` logs
  when an overlay has hooks that are off;
- `read_tree(overlay, keep=False) -> (digest, files)` (the bytes the digest covers when `keep`),
  `snapshot(user_store, overlay, digest, files) -> Path` — the copy a hook runs from, made once
  per digest under `<user store>/.cache/hook-snapshots/` from exactly the bytes hashed (what
  `AOS` excludes from a digest, `node_modules` and `__pycache__` included, is not in it), so a
  file swapped after hashing, or a planted module, does not run; `fresh_digest(overlay)` (never memoized),
  `overlay_plan(overlay, harness, user_store) ->
  plan` (`hook_plan` plus `digest`, `identity`), `declares_guard(overlay)`,
  `refusal(overlay) -> Optional[str]` (a guard that is off keeps its overlay out of
  `_activate.active`), `planned(overlays, harness, user_store)`, `has_live_pre_env(scope,
  harness_id)` (`env --cache` is not replayed for it).
- `class Subject`, `execute(subject, event, payload, env, *, limits=None) -> run`,
  `printable(data, bound=4096)` (stderr made fit for a log), `class Fired` (`result`, `reports`,
  `errors`: what each hook wrote to standard error, for the log, `decision`), `session_id(harness, native_id)` (a hash,
  never the harness's id), `value_for_match`, `fire(event, facts, plans, harness, *,
  environment, limits=None) -> Fired`, `unsupported_reports(plans)`, `describe(report)`,
  `pre_env(plans, session, base, harness, *, cwd=None) -> (reports, non_defaults)`.
- Wiring: `STATUS_PREFIX`, `FORWARDER = "overlay_hook.py"`, `wanted_events(store, harness) ->
  {event: guarded}`, `level_token(dest, user_store)` (`user` or `project-<hash>`: the only thing
  in an entry's text besides the harness and the event), `sh_quote` / `ps_quote` (single-quote a
  path for bash / PowerShell), `code_root()`, `forwarder_record(user_store, guards)`,
  `record_path`, `write_record`, `remove_record`, `interpreter_problem(config_dir, token)` (the
  recorded interpreter or dotagents is gone). The forwarder and its record live in the user's
  harness config directory only; the entry runs `<python> -I <forwarder> <harness> <event>
  <token>`.
- Sessions: `session_base(environ)` — the environment a hook starts from: what `dotagents
  launch` recorded in `AGENTS_SESSION_BASE` (each changed name's earlier value, null if unset)
  undone, and the harness's own variables (`CLAUDE_ENV_FILE`, `CLAUDE_CODE_*`, `CODEX_*`)
  dropped; `log_path`, `log_errors(user_store, errors)` and `LOG_LIMIT` — a hook's standard
  error goes to `<user store>/.cache/hooks.log`, never to the harness.

## `dotagents._hook_dispatch`

The program a harness's own hook runs (`dotagents hook`). Nothing a hook printed reaches the
harness: the answer is built from the output that was accepted, and can only deny, ask or
block.

- `MAX_NATIVE`, `tool_kind(harness, name) -> kind` (a tool not placed is `other`, which runs
  every hook of its event), `facts_of(harness, event, native) -> known`, `context_text(parts)`
  (each part inside a marker with a nonce chosen after the hooks ran),
  `write_env_block(path, label, changes)` (single-quoted values of validated names into
  Claude's env file; the file is split at line feeds only and a value holding any of
  `LINE_SEPARATORS` is refused, `env_file_safe(changes)`), `hook_environment(scope, overlay,
  environ)` (the overlay's process environment from `session_base`, with the user store's Python,
  never a project's venv), `answer(harness, event, fired, env_file) -> (stdout, stderr)`,
  `refusal_answer(harness, event, why)`, `plans_for(scope, harness, level, event=None)`,
  `dispatch(harness_id, event, stdin, scope, level, environ) ->
  (stdout, stderr)`.

## `dotagents.cli.hook`

`Hook` — the `hook` command (see the command table). Packaged beside the harness's hook
configuration by `init`: `_overlay/dotagents/hooks/overlay_hook.py`, the forwarder that finds
`dotagents`, runs `dotagents hook` with the payload, and answers a refusal itself when it
cannot (`--guard`).

## `dotagents._hooks`

Settings schema (Claude Code and Codex): `hooks.<Event>` is a list of matcher
objects `{"matcher"?: str, "hooks": [{"type": "command", "command": str, ...}]}`.

- `STATUS_PREFIX = "dotagents: "` — our hooks' `statusMessage` is
  `STATUS_PREFIX + label`; that label is the hook's identity.
- `build_hook_entry(command, *, matcher=None, status_message=None, shell=None,
  command_windows=None, timeout=None) -> dict` — one matcher object; `None` keys omitted;
  `command_windows` is written as `commandWindows`.
- `merge_hook(existing, command, *, matcher=None, status_message=None, shell=None,
  command_windows=None, timeout=None) -> tuple[list, bool]` — `(entries, changed)`. An identical
  entry is kept; ours in another shape (same status label, or the bare label on a
  command running dotagents) is refreshed in place, keeping keys the user added;
  duplicates dropped; foreign and malformed entries kept verbatim; a foreign hook
  sharing an entry with ours keeps the entry, ours moves out. Never raises.
- `remove_hook(existing, status_message) -> tuple[list, bool]` — drop ours;
  entries left empty are dropped.
- `load_settings(path) -> dict` — `{}` when missing or empty; invalid JSON raises
  `SystemExit`.
- `write_settings(path, data, *, dry_run=False) -> None` — 2-space JSON, non-ASCII
  kept, LF, atomic.

## `dotagents._skills`

- `PUBLISHED_RECORD = ".dotagents-published.json"` — in the shared skills dir: the
  tree digest of each skill published as a copy.
- `class SyncResult(success, mode, message="")` — truthy iff `success`; `mode`
  `"symlink"` / `"copy"` / `"conflict"` / `"error"` / `"none"`.
- `sync_path(source, target, *, prefer_symlink=True, force=False) -> SyncResult` —
  symlink (else copy) `source` at `target`; an existing correct link or matching copy
  succeeds unchanged; anything else there is a conflict unless `force`.
- `unsync_path(target, source) -> SyncResult` — remove `target` only when it is a
  link to `source` or a copy matching it.
- `resync_path(source, target, *, prefer_symlink=True, published_digest=None,
  overwrite=False) -> SyncResult` — refresh a copy only when it still matches
  `published_digest`, or with `overwrite`.
- `publish_overlay_skills(overlay_dir, shared_skills, *, copy=False, logger=None) ->
  int` — each of the overlay's skills (`Overlay.skills()`) into `shared_skills`; an
  occupied target is kept with a warning. Returns the count published.
- `harness_excluded(store, harness_id) -> set[str]` — the skills of `store`'s
  overlays whose `harness.<harness_id>.skills = false`: not linked into that
  harness's own skills dir (the context's listing names them).
- `resync_overlay_skills(overlay_dir, shared_skills, *, copy=False, overwrite=False,
  logger=None) -> int` — publish new skills, refresh unedited copies.
- `owned_overlay_skills(overlay_dir, shared_skills, *, logger=None) -> list[str]` —
  the published names that are this overlay's (link to its skill, or matching copy).
- `unpublish_skills(shared_skills, names, *, logger=None) -> int` — remove them, then
  drop broken links and an emptied dir.
- `remove_overlay_skills(overlay_dir, shared_skills, *, logger=None) -> int` —
  `owned_overlay_skills` + `unpublish_skills`.
- `clean_broken_syncs(shared_skills, logger=None) -> None` — drop dangling symlinks.
- `LINKED_RECORD = ".dotagents-linked.json"` — in an agent's own skills dir: the
  names each store (keyed by a hash of its path) linked or copied there, with each
  copy's digest.
- `class LinkResult` — `linked`, `copied`, `removed`, `kept` (lists of names) and
  `owned` (every name dotagents holds in the target afterwards).
- `link_skills_into(shared_skills, target_dir, *, dry_run=False, logger=None,
  exclude=()) -> LinkResult` — per skill (a name in `exclude` treated as one the
  store does not have): a symlink, else (Windows) a junction, else a copy; an
  unedited copy is refreshed, a skill the store dropped is removed (a dangling link
  too); someone else's entry, or a copy edited since, is kept with a warning.
  Junctions are treated as links throughout and removed without touching their
  target.
- `linked_names(shared_skills, target_dir) -> list[str]` — the names the record
  says `shared_skills` owns in `target_dir`.

## `dotagents._wrappers`

- `POSIX_TEMPLATE` — the `sh` wrapper body, for a `.pyz` (absolute, or relative
  to the wrapper) or `-m dotagents`. It runs the recorded interpreter; where that
  does not run (a Windows store read from WSL) it takes the store's agents' venv
  for this OS and machine (`.pyvenv/*-<os>-<arch>/bin/python`), else `python3`,
  and turns a recorded `C:/...` pyz path into a local one with `wslpath`.
- `write_wrappers(bin_dir, pyz_path, python=None, *, relative=False) -> list[Path]` —
  `dotagents` (sh) and `dotagents.cmd`, both always, running the `.pyz`.
- `write_module_wrappers(bin_dir, python=None) -> list[Path]` — both forms running
  `"<python>" -m dotagents` (`python` defaults to `sys.executable`).
- `wrapper_points_at_pyz(bin_dir) -> bool`.
- `check_path_warning(bin_dir) -> Optional[str]` — a warning with the PATH line to
  add, or `None` when `bin_dir` is on `PATH`.

## `dotagents._pyvenv`

One venv per store, `<store>/.pyvenv/<X.Y.Z>-<os>-<arch>/`, made from the running
interpreter; layered project > user > system through a `.pth`. `.pyvenv/` holds the
agents' venvs only (skills, overlay helpers, setup scripts), never a project's own
code. `AGENTS_NO_PYVENV=1` (or `true`/`yes`/`on`) turns it all off.

- `BASE_REQUIREMENTS = ("duho>=0.7.9,<0.8",)` — always in the user venv; dotagents'
  own duho range. `PYVENV_DIR = ".pyvenv"`, `STATE_FILE = ".dotagents-venv.json"` (in the venv:
  `{"digest", "requirements", "pth"}`), `PTH_FILE = "dotagents-layers.pth"` (in its
  site-packages), `DISABLE_ENV = "AGENTS_NO_PYVENV"`.
- `sync_venvs(scope, *, dry_run=False, logger=None) -> list[Path]` — the user venv
  (the base requirements), then the project's when `wants_venv`. pip runs only
  when the base changed, with `--find-links` from `AGENTS_PIP_SOURCE`. Overlays'
  dependencies are built into their environments by `_deps`, not here. The `.pth`
  is rewritten when it differs: the store's own libs and its shared Python
  environment, then each lower store's venv site-packages, libs and shared
  environment, then `runtime_entry()`. `write_layers(scope)` rewrites only the
  `.pth` of each venv a sync made (after a shared environment was rebuilt). Each venv's launchers (`_launchers.sync`) serve its
  store's overlays and every lower store's; a project venv a sync made that is no
  longer wanted keeps its launchers current too. With `AGENTS_NO_PYVENV` it logs
  which scripts get no launcher. Raises `VenvError` or `LauncherError` (both
  `SystemExit`) on failure.
- `wants_venv(scope, level, store, libs) -> bool` — always for `user`; for `project`
  when its overlays have Python dependencies, libs or scripts; never for `system` (a venv an
  administrator made there is still layered).
- `store_overlays(scope, stores) -> list[Overlay]` — the overlays in play
  installed in any of `stores`.
- `ensure_venv(target, python=None, *, with_pip=None, dry_run=False, logger=None)
  -> Optional[Path]` — the venv's interpreter, created when missing; `with_pip=None`
  tries ensurepip and falls back to `--without-pip`, `False` never uses it.
- `pip_command(vpy, *, cache_dir, logger=None) -> list[str]` — the venv's own
  `-m pip`, else `[sys.executable, <pip wheel>/pip, "--python", <vpy>]`.
- `active_venv(scope, environ=None) -> Optional[Path]` — the highest store's venv
  for the running interpreter that a sync completed (its `STATE_FILE` exists); a
  venv made there any other way is never an agents venv.
- `layers(scope) -> list[tuple[str, Path, list[Path]]]` — `(level, store, libs)`,
  highest first (an overlay's libs: its `PYTHONPATH` additions).
  `check_dependencies(path, locked, lock_digest) -> list[str]` (requirement lines
  only, a lock pinned with hashes and matching its `lock_digest`).
  `runtime_entry() -> str` (the `.pyz`, else the dir holding `dotagents`).
  `venv_dir(store)`, `venv_python(venv)`, `site_packages(venv)`, `triplet()`,
  `os_bucket()`, `read_state(venv)`, `disabled(environ=None)`,
  `is_pyvenv_python(path)`.

## `dotagents._deps`

Overlays' dependency environments. An overlay's dependency files
(`_aos.dependencies_of`) go into the environment its runtime variant names with
`environment`: `shared` (default) or a name, per store, or `isolated`, per
overlay. Python: `<store>/.pyvenv/<triplet>/envs/{shared,named/<n>,isolated/<id>}/<generation>/`,
filled by `pip install --target` (the venv's pip; `sys.executable`'s with
`AGENTS_NO_PYVENV`); the shared one is in the venv's `.pth`, a named or isolated
one (and the shared one without a venv) goes on `PYTHONPATH` after `lib/python/`.
Node.js: `<store>/.nodeenv/<os>/<kind>/<generation>/node_modules`, on `NODE_PATH`
after `lib/node/`. A `current` file beside the generations names the build in use.

- `rebuild(scope, store, key, *, exclude=(), dry_run=False, logger=None) ->
  list[str]` — build environment `key` again into a new generation from every
  member's file at once, in composition order (members: the store's overlays not
  refused but for their preparation, less `exclude`), and switch to it only when
  it succeeded; an environment left with no member is deleted. Python: one pip
  run, `--require-hashes --no-deps --only-binary=:all:` when every file is a lock,
  else the files' requirements with their hashes left out. Node.js: `npm ci` from
  a lone locked member's `package.json` and lock, else `npm install` from one
  `package.json` holding every member's dependencies, from copies only. Builds
  once per process for unchanged inputs. Returns the identities a conflict
  blocks; raises `DependencyError`.
- `conflicts(overlays) -> dict[identity, why]` — overlays of one environment in
  one store asking for a package at different versions.
- `dependencies(overlay)`, `keys_of(overlay)`, `members(overlays)`,
  `store_members(scope, store, *, exclude=())`, `packages_for(overlay) -> dict`
  (what a session adds), `packages_dir(store, key) -> Optional[Path]`,
  `env_dir(store, key)`, `runtime_root(store, runtime)`, `kind(key)`,
  `npm_command() -> Optional[list[str]]` (Windows: `node` and npm's
  `npm-cli.js`, never `npm.cmd`), `signature(scope)` (the env cache's input),
  `CURRENT`, `BUILD_TIMEOUT`, `class DependencyError`.

## `dotagents._pipwheel`

The pip wheel for a venv with no pip of its own.

- `PIP_VERSION = "25.3"` (the last pip for Python 3.9), `PIP_WHEEL`, `PIP_SHA256` —
  every source must match the hash. `PIP_SOURCE_ENV = "AGENTS_PIP_SOURCE"`.
  `VENDORED = ("_vendor", PIP_WHEEL)` (inside the package),
  `DEFAULT_INDEX = "https://pypi.org/simple"`.
- `pip_wheel(cache_dir, *, logger=None, environ=None) -> Path` — from, in order: the
  cache file, the vendored copy (`build-pyz --with-pip`), `AGENTS_PIP_SOURCE` and pip's
  `find-links` (a dir, `file:`/`http(s):`, or a pathapi-uri URI), then the index
  (`PIP_INDEX_URL`, else pip's config `index-url`, else PyPI; PEP 691 JSON, else PEP
  503 HTML), honouring pip's `cert`, `trusted-host`, `proxy` and credentials in the
  URL. Raises `PipUnavailable` (a `SystemExit`) naming every source, redacted.
- `find_links(environ=None, *, cache_dir, logger=None) -> list[str]` —
  `AGENTS_PIP_SOURCE` for pip's `--find-links`, a URI pip cannot read copied locally.
- `pip_config(environ=None) -> dict[str, str]` — `index-url`, `find-links`, `cert`,
  `trusted-host`, `proxy` from `PIP_*` over pip's config files.

## `dotagents._fs`

- `write_text_lf(path, text, *, atomic=False) -> Path` — UTF-8, LF line endings on
  every platform, parent dirs created; `atomic` writes a sibling temp file and
  `os.replace`s it. Every file dotagents writes goes through this.

## Package data

- `_overlay/dotagents/templates/AGENTS.md`, `PROJECT.md` — the user and project
  block templates (`{{AGENTS_MD}}` placeholder).
- `_overlay/dotagents/cmds/findings.py`, `launch.py` — the bundled command modules
  (always a discovery source; never copied into a store). `launch` exports
  `AGENTS_CONTEXT_FILE` and writes the context to
  `<user store>/.cache/launch/<agent>-<sha256[:12] of the project root, or "-g">.md`.
- `_overlay/dotagents/hooks/` — `sessionstart_codex_context.py`,
  `pretooluse_codex_env.py`, `preinvocation_antigravity_context.py`: copied into the
  harness's config dir by `wire_hooks`; standard library only.

## Environment variables

Prefix rule: `AGENTS_*` are non-secret and may be printed; `DOTAGENTS_*` are
tool-internal and never printed (`env` leaves inherited ones out of its full output).

Read:

- `AGENTS_HOME` — the user store. `AGENTS_PROJECT_ROOT`, then `CLAUDE_PROJECT_DIR` —
  the project root pin. `AGENTS_SYSTEM_ROOT` — the system store.
- `AGENTS_OVERLAYS_REPO`, `AGENTS_OVERLAYS_REPO_<KEY>` — overlay repos (after
  `--repo`). `AGENTS_CMDS_PATH` — extra command dirs.
- `AGENTS_HARNESS` — the running harness (registry name or harness id), for
  identity. `AGENTS_PYTHON` — the interpreter for `env.py` and a `python` setup.
  `AGENTS_PYTHONPATH` — the agent library dirs, highest precedence first.
  `AGENTS_NO_PYVENV` — turns the scope venvs off. `AGENTS_PIP_SOURCE` — a directory
  of wheels (or a URI to one) for pip, before the index. `PIP_INDEX_URL`,
  `PIP_CONFIG_FILE`, `PIP_FIND_LINKS`, `PIP_CERT`, `PIP_TRUSTED_HOST`, `PIP_PROXY` and
  pip's config files — read to fetch the pip wheel.
  `AGENTS_PYLIB` — the `.pyz` dotagents runs from (duho and pathapi import
  from it), unset for an installed dotagents.
- Harness config dirs: `CLAUDE_CONFIG_DIR`, `CODEX_HOME`, `PI_CODING_AGENT_DIR`.
  Harness markers: `CLAUDECODE`, `CLAUDE_CODE_ENTRYPOINT`, `GEMINI_CLI`,
  `CODEX_SANDBOX*`, `CURSOR_AGENT`, `PI_CODING_AGENT`. Model vars: `ANTHROPIC_MODEL`,
  `GEMINI_MODEL`, `OPENAI_MODEL`, `CURSOR_DEFAULT_MODEL`, `COPILOT_MODEL`,
  `PI_MODEL`.
- Proxy: `AGENTS_PROXY`, `AGENTS_WEBFETCH_PROXY_URL`, `HTTP(S)_PROXY`, `ALL_PROXY`,
  `NO_PROXY` (either case).
- In the hooks: `CLAUDE_ENV_FILE`, `CLAUDE_CODE_GIT_BASH_PATH`, `AGENTS_RUNTIME_SET`
  (the env loaders' once-per-process guard), `DOTAGENTS_HOOK_SHELL=posix|windows`
  (overrides the Codex env hook's platform check).

Emitted by `env` (and applied by `launch`): `AGENTS_HARNESS`, `AGENTS_VENDOR`,
`AGENT`, `AGENTS_MODEL`, `AGENTS_HOME`, `AGENTS_PROJECT_ROOT`, `AGENTS_PYTHON`,
`AGENTS_PYVENV` (with a scope venv), `AGENTS_PYLIB` (from a `.pyz` only), `<NAME>_OVERLAY_ROOT`, `<NAME>_OVERLAY_STATE`, `PYTHONPYCACHEPREFIX`, `PATH`, `PYTHONPATH`, `AGENTS_PYTHONPATH`, `AGENTS_PROXY` and the mirrored
proxy vars, plus what overlays declare and the stores' env files set. `AGENTS_AGENT` is never emitted.
`launch` also exports `AGENTS_CONTEXT_FILE`.

Set for an overlay's setup and env scripts: `AOS_OVERLAY_ROOT`,
`AOS_OVERLAY_NAME`, `AOS_OVERLAY_STATE` (its state directory, where it writes),
`<NAME>_OVERLAY_STATE`; setup also gets `AOS_SETUP_PHASE` (`install`, `sync`
or `remove`, also its first argument), `AGENTS_HOME` (user store),
`AGENTS_SCOPE_ROOT`, `AGENTS_SCOPE`. `AOS_*`, `*_OVERLAY_ROOT` and
`*_OVERLAY_STATE` are dotagents': no overlay can set one.

Read by a script launcher (`_launchers`): its overlay's `<NAME>_OVERLAY_ROOT`.

## Gotchas

- **Python 3.9 floor.** Runtime-evaluated annotations need `from __future__ import
  annotations` for `X | Y`; `Path.write_text(newline=)` is 3.10+ (use
  `_fs.write_text_lf`).
- **Inside a `.pyz`** `Path(__file__)` is not a real file: package data is reached
  through `_resources._package_data_dir(name)` (extracted once to a per-process
  scratch dir removed at exit); `duho` reads the command modules' flags and help
  through the zip importer.
- **Writes are LF-only.** Use `_fs.write_text_lf`, never `Path.write_text`.
- **Skills run under `AGENTS_PYTHON`**, the scope venv: duho, every overlay's
  requirements and libs, and dotagents import there. Its site-packages is never put
  on `PYTHONPATH`, which every other Python in the session would read first.
- **`env` output is sensitive**: it prints resolved values, granted secrets
  included. Nothing in the package logs a value, only names.
- **`overlay.toml` is not read.** An overlay's manifest is `overlay.cfg`; a
  manifest that does not validate is ignored whole, with a warning.
- **A refused manifest or a refused overlay contributes nothing**: no
  environment, context, skills, commands' libs, launchers or MCP servers.
- **Credentials in a repo URL** are removed from every log line, cache dir name and
  install record; an overlay installed from such a URL syncs only while that repo is
  configured again (or given as `--repo`).
