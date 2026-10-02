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
- **overlay** — a directory installed as `<store>/overlays/<name>/`; its root is
  exported as `$<NAME>_OVERLAY_ROOT`.

`dotagents.__version__` is the package version.

## Command line

`dotagents <command> [options]` (also `python -m dotagents`, or a built `.pyz`).
`dotagents --help` / `<command> --help` list every flag. Umbrella options:
`--cmdspath DIR` (repeatable), `--loglevel`, `-v` / `-q`. An umbrella run with no
subcommand (`dotagents`, `dotagents overlays`, `dotagents findings`) prints its usage
and an error saying a command is required, and exits 2.

| Command | Contract |
| --- | --- |
| `init [-g] [--dest D] [--from SRC] [--bin-dir D] [--agents a,b] [--no-hooks] [--powershell-env-hook] [--force] [--dry-run]` | Merge the base block into `<store>/AGENTS.md`, write `<store>/bin/dotagents[.cmd]`, and per active agent (`--agents`, else detected + `claude`) write its include and hooks. `--force` replaces `AGENTS.md`'s content, backed up under `<store>/install_backup/<timestamp>/`. `--from` records its base in `<store>/dotagents/config.toml`. |
| `overlays add NAME... [-g] [--repo SPEC]... [--copy] [--no-setup] [--setup-timeout S] [--no-requires] [--prune] [--dry-run]` | Install each overlay and its `requires` into `<store>/overlays/<name>/` (an installed one is made to match its source again, as `sync` does; `--prune` clears its ignored files too), publish its skills, run its `setup.py`, recompose the managed block. Every name is resolved before anything is written; a source inside `<store>/overlays/` is refused; a fresh install whose setup fails is rolled back. No name: exit 2. |
| `overlays remove NAME... [-g] [--force] [--dry-run]` | Delete the overlay dirs, unpublish the skills they published, recompose the block. Refuses an overlay another installed overlay requires unless `--force`. No name: exit 2. |
| `overlays sync [GLOB] [-g] [--repo SPEC]... [--copy] [--prune] [--no-setup] [--setup-timeout S] [--dry-run]` | Refresh installed overlays from the repo recorded at install (`--repo` replaces it): the install is made to match the source exactly: new and changed files land, `overlay.toml` is refreshed, files the source does not ship are removed; a file edited here is backed up first under `<store>/install_backup/<timestamp>/overlays/<name>/`. What the `.gitignore` / `.ignore` at the overlay's root match (setup output, local state) and tool caches are left alone; `--prune` clears them too. Installs new `requires`. Exits 1 when an overlay's repo cannot be loaded, its source lies inside `<store>/overlays/`, or its setup fails. |
| `overlays list [-g] [--repo SPEC]... [--json]` | Installed overlays per store (shadowed copies marked, unmet `requires` flagged) and what the repos offer. |
| `overlays show NAME [-g] [--repo SPEC]... [--json]` | One overlay: the copy a session would use, else the source's; manifest, setup, skills, file count, recorded source. No name: exit 2. |
| `context [OUT] [-g] [--agents a,b] [--format markdown\|system-reminder\|json] [--write-agent] [--inline]` | Print (or write to `OUT`) the assembled context. `--write-agent` merges it into each agent's `context_target` under the project root (not with `-g`). Exits 2 when `--agents` names no known agent. |
| `path [-g] [--lib] [--format list\|native\|posix\|json]` | Print the bin dirs `env` puts on `PATH` (`--lib`: the existing lib dirs it puts on `PYTHONPATH`), highest precedence first; runs no env file. `posix` is `:`-joined, MSYS2 form on Windows. |
| `env [-g] [--format F] [--diff] [--cache] [--into FILE]` | Print the assembled environment (`--diff`: only what differs from the caller's; `--cache`: replay the previous output while `env_cache_key` holds, up to `ENV_CACHE_TTL`; `--into FILE`: write it into FILE as the one block this command owns there) in format `F`: `auto` (default: the calling shell), `export`/`posix`/`sh`/`bash`, `dotenv`/`env`, `powershell`/`pwsh`/`ps`, `cmd`/`bat`/`batch`, `fish`, `json`, `ini`, `yaml`. |
| `launch [AGENT] [-g] [--command PROG] [--no-context] [--inline] [--write-agent] [--dry-run] [-- ARGS...]` | Run the agent's CLI with the env applied and the context handed over; the exit code is the harness's (`128+N` on signal N). |
| `findings add\|list\|show\|done\|reopen\|remove\|index\|path [-g] [--dir D]` | The findings queue at `<store>/findings/` (`--dir` overrides): one markdown file per finding, `done` moves it to `processed/` with a required resolution, `INDEX.md` regenerated on every change. |
| `about [--json]` | `dotagents-cli <version>`, then `<distribution> <version>` per bundled (`.pyz`) or installed package. |
| `build-pyz [--out P] [--python SHEBANG] [--duho-version V] [--pathlib-next-version V] [--extras a,b]` | Build a zipapp from a source checkout; errors elsewhere. |

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
  `python -m dotagents`, `install.py`).
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

## `dotagents._ignore`

The `.gitignore` / `.ignore` at the ROOT of a directory, read as git reads a
`.gitignore` (pure stdlib) -- what an installed overlay keeps as its own when `add`
/ `sync` mirror its source. An ignore file in a subdirectory is an ordinary file;
nothing above the root is read.

- `IGNORE_FILES = (".gitignore", ".ignore")` — read at the root in that order, so
  `.ignore` wins (ripgrep's precedence).
- `parse(text, base="") -> list` — one file's rules (`base`: its directory):
  comments, `\#` / `\!` escapes, `!` negation, leading/middle `/` anchoring,
  trailing `/` directory-only, `*`, `?`, `[...]`, `**`.
- `class IgnoreRules(rules=())` — `for_root(root)` reads `root`'s two files;
  `ignored(rel, is_dir=False) -> bool` decides a `/`-joined path (a pattern without
  a slash matches at any depth), a path under an ignored directory staying ignored
  whatever a later `!` says, as in git.

## `dotagents._resources`

The package's own data; library code, importing nothing from `dotagents.cli`.

- `BASE_ROOT: Path` — the bundled base overlay dir (`_overlay/`), extracted to a
  per-process scratch dir when running from a `.pyz`.
- `BASE_AGENTS_TEMPLATE = "dotagents/templates/AGENTS.md"`,
  `BASE_PROJECT_TEMPLATE = "dotagents/templates/PROJECT.md"`,
  `AGENTS_MD_PLACEHOLDER = "{{AGENTS_MD}}"`.
- `base_agents_text(src, dest, *, project=False) -> str` — the base block for the
  store at `dest`: the project template for a project store when the base has one,
  else the user template, else `<src>/AGENTS.md`; `{{AGENTS_MD}}` rendered as the
  absolute POSIX path of `<dest>/AGENTS.md`.
- `pyz_archive() -> str | None` — the `.pyz` this dotagents is imported from (the
  ancestor of the module's `__file__` that is a file), or `None` for a plain
  install. The archive also holds the vendored duho and pathlib_next; `env`
  exports it as `AGENTS_PYLIB`.

## Commands — `dotagents.cli.init`, `dotagents.cli.overlays`, `dotagents.cli.context`, `dotagents.cli.env`, `dotagents.cli.path`, `dotagents.cli.about`, `dotagents.cli.build_pyz`

One `duho` command class per command above; fields are the flags, `__call__()`
returns the exit code.

- `init.Init`; `overlays.Overlays` (umbrella) with `OverlayAdd`, `OverlayRemove`,
  `OverlayList`, `OverlaySync`, `OverlayShow`; `context.Context` (`FORMATS =
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
  by `build-pyz` into the package), `RUNTIME_PACKAGES = ("duho", "pathlib_next",
  "uritools", "netimps", "requests")` (the only packages a plain install reports);
  `bundle_manifest() -> Optional[dict]`, `installed_packages() -> dict[str, str]`,
  `packages() -> tuple[dict[str, str], Optional[dict]]` (the bundle's packages, else
  the installed ones; the CLI itself in neither).

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
    listed once), `overlays` (`Overlay.installed(*stores)`), `overlay_root`
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

- `DEFAULT_PRIORITY = 500`. `DEFAULT_SETUP_TIMEOUT = 300` — seconds a setup script
  may run when neither `--setup-timeout` nor the manifest's `setup_timeout` says
  otherwise; `0` from either means no limit.
- `parse_manifest_text(text, origin="overlay.toml") -> Optional[dict]` — the TOML
  document via `tomllib` / `tomli`, else a built-in reader (top-level strings,
  string arrays including multi-line strings, an integer); `None` plus one warning
  when it is not valid TOML.
- `class InstallResult` — what `install_to` did: `written`, `unchanged`,
  `replaced` (edited here, replaced by the source's file), `removed` (not in the
  source, or cleared by `prune`), `backed_up`, `lines`; property `skipped`
  (= unchanged). Unpacks as `(written, skipped, lines)`.
- `class Overlay(path, store=None)` — one overlay directory; `os.PathLike`; equal
  and hashed by `path`; nothing read at construction.
  - Class data: `NAME_RE`, `RESERVED_NAMES` (Windows device names),
    `MANIFEST_NAME = "overlay.toml"`, `SETUP_SCRIPT_NAMES = ("setup.py",)`,
    `SKIP_PARTS` (`.git`, `__pycache__`, tool caches, `node_modules` — never
    installed), `INSTALL_RECORD = ".dotagents-install.json"`, `DEFAULT_PRIORITY`.
  - `Overlay.is_valid_name(name) -> bool` — `NAME_RE` (a letter, then letters,
    digits, `_`, `.`, `-`, ending in a letter or digit), not a `LEVEL_NAMES` entry,
    not a Windows device name (with or without an extension).
  - `Overlay.normalize_name(name) -> str` — lowercase, `_` and `.` → `-`: the
    canonical name, the install dir name, the name compared everywhere.
  - `Overlay.root_var_for(name) -> str` — `normalize_name(name)` upper-cased, every
    non-alphanumeric → `_`, plus `_OVERLAY_ROOT`.
  - Properties: `name` (dir name), `normalized_name`, `root_var`, `is_valid`,
    `manifest_path`, `priority` (manifest value, else `DEFAULT_PRIORITY`),
    `sort_key` (`(priority, manifest name, dir name)`), `install_record_path`;
    attribute `store` (the store it was discovered in, else `None`).
  - `Overlay.discover(root, store=None) -> list[Overlay]` — valid-named dirs
    directly under one `overlays/` root, sorted; warns when two normalize alike.
  - `Overlay.installed(*stores) -> list[Overlay]` — `discover` over each
    `<store>/overlays` in order (`None` skipped), each result stamped with its
    store; an overlay whose normalized name appears in a later store is dropped
    (the later copy shadows it).
  - `Overlay.sort_by_priority(overlays) -> list[Overlay]` — sorted by `sort_key`.
  - `read_manifest() -> dict` — keys `name`, `description`, `routing`, `rules`,
    `requires` (valid names only, normalized, de-duplicated), `priority`; a missing
    or invalid manifest gives empty contributions.
  - `find_setup_script() -> Optional[Path]`.
  - `setup_timeout(override=None) -> int` — `override`, else the manifest's
    `setup_timeout` (a non-negative int; anything else is ignored with a warning),
    else `DEFAULT_SETUP_TIMEOUT`; `0` = no limit.
  - `run_setup(*, agents_dir, dry_run, logger, scope_root=None, scope_level=None,
    base_env=None, timeout=None) -> Optional[int]` — runs `setup.py` under `sys.executable` with
    cwd = the overlay dir and env `base_env` (default `os.environ`; `overlays` passes
    the scope's assembled env) plus `AGENTS_HOME=agents_dir`, `AGENTS_SCOPE_ROOT=scope_root or
    agents_dir`, `AGENTS_SCOPE=scope_level` (when given), `AGENTS_OVERLAY_DIR`;
    returns its exit code, `None` when there is no script, `0` on `dry_run`, `124`
    when it ran past `setup_timeout(timeout)` and was stopped.
  - `files() -> list[Path]` — every file to install: not the manifest, not the
    install record, not under `SKIP_PARTS`, not `*.pyc`.
  - `read_install_record() -> dict` — `{"source": dict | None, "files": {rel:
    sha256}}` of an installed copy; `{}` when absent.
  - `rule_blocks(rel_paths) -> tuple[list[str], list[str]]` — `(blocks, warnings)`:
    each file's leading `- **` bullets up to its next `## ` heading; a path outside
    the overlay, missing or unreadable is a warning.
  - `ignore_rules() -> IgnoreRules` — the rules of the `.gitignore` / `.ignore`
    at the overlay's root (`dotagents._ignore`).
  - `install_to(dest_overlay_dir, dry_run, *, prune=False, backup_root=None,
    source=None) -> InstallResult` — make the install exactly the overlay:
    `files()` installed or replaced, `overlay.toml` refreshed, every other file
    removed; what `ignore_rules()` matches, the install record and `SKIP_PARTS`
    caches are never copied, replaced or removed unless `prune` (which clears
    them, without backup); a replaced or removed file that is not what dotagents
    installed is copied under `backup_root` first; writes the install record
    (`source`, or the previous one when `None`).
- `OVERLAY_ROOT_NOTE: str` — the line emitted once above overlay routing lines that
  use `$<NAME>_OVERLAY_ROOT`.
- `recompose_overlay_block(agents_md, base_block, overlays, dry_run, logger) -> bool`
  — rebuild the managed block from the pristine `base_block` over `overlays` in
  `sort_key` order; creates the file when absent; content outside the markers kept;
  returns whether it changed (`False` and a warning when the file has no block).

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
  location or ref starts with `-`.
- `redact(text) -> str` — URL userinfo removed, query values masked.
- `uri_path_class()` — `pathlib_next.uri.UriPath`, or `None` without the `uri` extra.
- `url_scheme(location) -> str` — the lower-cased scheme.
- `GIT_SSL_REVOKE_KEY = "git_ssl_revoke"`, `GIT_SSL_REVOKE_VALUES = ("best-effort",
  "true", "false")`, `GIT_SSL_REVOKE_DEFAULT = "best-effort"`;
  `git_ssl_revoke(*stores) -> str` — the first store config (`config.toml`) that sets
  the key, project store first; another value raises `SourceError` naming the file.
- `class SourceCache(root, logger=None, ssl_revoke="best-effort")` — every git call
  runs as `git -c http.schannelCheckRevoke=<ssl_revoke> ...` (Git for Windows'
  Schannel checks revocation; git with OpenSSL ignores it), and a failure that looks
  like a revocation check names the key. `repo_dir(location, ref=None) -> Path`
  (`<root>/<slug>-<hash>`); `checkout(spec) -> Path` (clone or fetch, once per
  process; a failed fetch of an existing checkout is a warning);
  `materialize(spec) -> Path` (a `url` spec: `file://` in place, anything else
  synced into `<root>/uri/` once per process; needs the `uri` extra). Creates a
  `.gitignore` of `*` in `root`.
- `parse_document(text, suffix, origin) -> dict[str, str]` — a registry's
  name → spec map (the document or its `overlays` table); TOML needs Python 3.11+ or
  `tomli`, YAML `pyyaml`.
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

Assembly order (`get_environment`): identity (`stamp_identity`), then the roots,
then `PATH`, then `PYTHONPATH` / `AGENTS_PYTHONPATH`, then the env files, then the
proxy model.

- Roots: `AGENTS_HOME` and `AGENTS_PROJECT_ROOT` name the stores walked, absolute —
  an inherited value naming the same directory holds, another is replaced; in a
  user-scope walk `AGENTS_PROJECT_ROOT` is set only when unset. Only when unset:
  `AGENTS_PYTHON` (`sys.executable`) and one `<NAME>_OVERLAY_ROOT` per
  `scope.overlays` (a value pinned to the user store's copy is re-pointed when a
  project copy shadows it). `AGENTS_PYLIB` (`_env.PYLIB_VAR`): the `.pyz` when
  dotagents runs from one (`_resources.pyz_archive()`), else not set — where its
  own libraries (duho, pathlib_next) import from by zipimport for an overlay
  built on them. Never added to `PYTHONPATH`: a consumer appends it to
  `sys.path` when its import fails.
- `PATH`: every level's `bin` (contract A, not project-root, missing dirs included)
  at the front in that order, whatever the caller's `PATH` held; empty and
  cwd-relative inherited entries are dropped.
- `PYTHONPATH`: every existing level's `lib` at the front, highest precedence
  first; the entries an inherited `AGENTS_PYTHONPATH` named (another scope's libs)
  are removed first. `AGENTS_PYTHONPATH`: the same list, `os.pathsep`-joined;
  removed when there are none.
- Env files: all `pre.env.py` / `pre.env` (+ the project store's `pre.local.env`),
  then all `env.py` / `env` (+ `local.env`), contract-A order, regular files only,
  each evaluated against everything before it. The project root contributes none.
  A layer that raises is skipped with a warning.
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
- `get_bin_paths(scope) -> list[Path]`, `get_lib_paths(scope) -> list[Path]`
  (existing dirs only), `get_overlay_roots(scope) -> list[Path]` (the dirs of
  `scope.overlays`), `resolve_env_files(scope) -> list[tuple[str, Path,
  Optional[Path]]]` (pre tier then main tier, as `Scope.paths` tuples).
- `apply_proxy_model(osenv) -> dict[str, str]` — the proxy changes described above.
- `ENV_CACHE_TTL = 300.0` (seconds). `env_cache_key(scope, base_env, *parts) -> str`
  — a digest of what an assembly reads without running it: roots, cwd, interpreter,
  package version, installed overlays, each env-file candidate's
  existence/mtime/size, every file under an existing `lib`, the whole `base_env`,
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
  `agent.loaded_paths(project_root)`. Sources in order: overlay `CONTEXT.md`s by
  `Overlay.sort_key`, then contract-A `AGENTS.md` (every store and the project root)
  and `AGENTS.local.md` (project store and project root), each prefixed
  `<!-- Source: <path> -->`. Then a skills listing (`- **name** (`<SKILL.md>`):
  description`, from each skill's leading frontmatter; a project skill wins), then
  a "Local additions to overlay files" section when `local_additions` finds any.
  `<PROJECT_ROOT>` and `<NAME_OVERLAY_ROOT>` placeholders expand; with
  `expand_vars` so do `$NAME_OVERLAY_ROOT` / `${NAME_OVERLAY_ROOT}`. `inline`
  appends the on-demand `.md` files the sources reference, each resolved against
  its own source's directory (a project's also against its `.agents`; a user or
  system source never into the project).
- `assemble_context_data(agent, scope, *, inline=False, expand_vars=True) -> dict`
  — `{"agent", "harness", "sources", "context", "skills": [{"name",
  "description", "path"}], "local_additions": [{"overlay_file", "local": [...]}]}`;
  `context` without the skills and local-additions listings.
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

## `dotagents._hooks`

Settings schema (Claude Code and Codex): `hooks.<Event>` is a list of matcher
objects `{"matcher"?: str, "hooks": [{"type": "command", "command": str, ...}]}`.

- `STATUS_PREFIX = "dotagents: "` — our hooks' `statusMessage` is
  `STATUS_PREFIX + label`; that label is the hook's identity.
- `build_hook_entry(command, *, matcher=None, status_message=None, shell=None,
  command_windows=None) -> dict` — one matcher object; `None` keys omitted;
  `command_windows` is written as `commandWindows`.
- `merge_hook(existing, command, *, matcher=None, status_message=None, shell=None,
  command_windows=None) -> tuple[list, bool]` — `(entries, changed)`. An identical
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
  int` — each `<overlay_dir>/skills/<name>` into `shared_skills`; an occupied target
  is kept with a warning. Returns the count published.
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
- `link_skills_into(shared_skills, target_dir, *, dry_run=False, logger=None) ->
  LinkResult` — per skill: a symlink, else (Windows) a junction, else a copy; an
  unedited copy is refreshed, a skill the store dropped is removed (a dangling link
  too); someone else's entry, or a copy edited since, is kept with a warning.
  Junctions are treated as links throughout and removed without touching their
  target.
- `linked_names(shared_skills, target_dir) -> list[str]` — the names the record
  says `shared_skills` owns in `target_dir`.

## `dotagents._wrappers`

- `POSIX_TEMPLATE`, `POSIX_TEMPLATE_REL` (pyz path relative to the wrapper),
  `POSIX_TEMPLATE_MODULE` (`"<python>" -m dotagents`) — the `sh` wrapper bodies.
- `write_wrappers(bin_dir, pyz_path, python=None, *, relative=False) -> list[Path]` —
  `dotagents` (sh) and `dotagents.cmd`, both always, running the `.pyz`.
- `write_module_wrappers(bin_dir, python=None) -> list[Path]` — both forms running
  `"<python>" -m dotagents` (`python` defaults to `sys.executable`).
- `wrapper_points_at_pyz(bin_dir) -> bool`.
- `check_path_warning(bin_dir) -> Optional[str]` — a warning with the PATH line to
  add, or `None` when `bin_dir` is on `PATH`.

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
  identity. `AGENTS_PYTHON` — the interpreter for `env.py`. `AGENTS_PYTHONPATH` —
  the overlay/store `lib` dirs `env` put on `PYTHONPATH`, highest precedence first.
  `AGENTS_PYLIB` — the `.pyz` dotagents runs from (duho and pathlib_next import
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
`AGENTS_PYLIB` (from a `.pyz` only), `<NAME>_OVERLAY_ROOT`, `PATH`, `AGENTS_PYTHONPATH`, `AGENTS_PROXY` and the mirrored
proxy vars, plus whatever the env files set. `AGENTS_AGENT` is never emitted.
`launch` also exports `AGENTS_CONTEXT_FILE`.

Set for an overlay's `setup.py`: `AGENTS_HOME` (user store), `AGENTS_SCOPE_ROOT`,
`AGENTS_SCOPE`, `AGENTS_OVERLAY_DIR`.

## Gotchas

- **Python 3.9 floor.** Runtime-evaluated annotations need `from __future__ import
  annotations` for `X | Y`; `Path.write_text(newline=)` is 3.10+ (use
  `_fs.write_text_lf`). On 3.9, `pathlib_next` needs `typing_extensions` installed.
- **Inside a `.pyz`** `Path(__file__)` is not a real file: package data is reached
  through `_resources._package_data_dir(name)` (extracted once to a per-process
  scratch dir removed at exit), and `cli.main()` repoints the built-in command
  modules' sources so `duho` still reads their flags and help.
- **Writes are LF-only.** Use `_fs.write_text_lf`, never `Path.write_text`.
- **`env` output is sensitive**: it prints resolved values. Nothing in the package
  logs a value, only names.
- **Credentials in a repo URL** are removed from every log line, cache dir name and
  install record; an overlay installed from such a URL syncs only while that repo is
  configured again (or given as `--repo`).
