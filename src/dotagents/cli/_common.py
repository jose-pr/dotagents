"""Helpers the CLI's command modules share.

`DotAgentsArgs` (the scope fields), `_write_stdout`, the store config that
`init --from` records, `init`'s body (`_apply_base`) and `--from` resolution.
Nothing here depends on the command classes or the umbrella, so command
modules import from here without an import cycle (`cli/__init__.py` imports
the command modules, not the reverse).

The package data (`BASE_ROOT`, `base_agents_text`, `_package_data_dir`,
`_scratch_dir`) lives in `dotagents._resources` and the block composition
(`_compose_block`, `OVERLAY_ROOT_NOTE`) in `dotagents._overlays`, so library
modules never import the CLI; they are re-exported here for the command
modules and tests that import them from this module.
"""

import os
import re
from pathlib import Path
from typing import Optional

from duho import Cmd, LoggingArgs

from dotagents._overlays import OVERLAY_ROOT_NOTE, _compose_block  # noqa: F401 (re-exported)
from dotagents._resources import (  # noqa: F401 (re-exported)
    AGENTS_MD_PLACEHOLDER,
    BASE_AGENTS_TEMPLATE,
    BASE_PROJECT_TEMPLATE,
    BASE_ROOT,
    _package_data_dir,
    _scratch_dir,
    base_agents_text,
)
from dotagents._scope import resolve_user_store


def _write_stdout(text: str) -> None:
    """Write to stdout as UTF-8, whatever the console's encoding claims to be.

    A bare `print()` encodes with the console codepage -- cp1252 on a default
    Windows shell -- so a single character outside Latin-1 (an arrow, a
    box-drawing rule, a curly quote, any emoji) raises UnicodeEncodeError and
    the command dies having emitted nothing. Context files routinely contain
    such characters, env values and finding descriptions can, and `context` is
    the SessionStart hook's payload, so the failure is both likely and silent.
    Write bytes through the underlying buffer instead, replacing anything even
    UTF-8 cannot represent rather than aborting.
    """
    import sys

    data = text.encode("utf-8", errors="replace")
    buffer = getattr(sys.stdout, "buffer", None)
    if buffer is None:  # pragma: no cover -- captured/replaced stdout in tests
        sys.stdout.write(text)
        return
    buffer.write(data)
    buffer.flush()


class DotAgentsArgs(LoggingArgs, Cmd):
    """Shared ``-g/--global`` + ``--agents-dir`` fields for any command whose scope
    is *where the store lives* (``_scope.resolve_scope``'s two axes). Subclass as
    ``class Foo(DotAgentsArgs): ...`` -- it already inherits ``LoggingArgs``/``Cmd``,
    do not list them again. A subclass may redeclare a field (same type/default)
    to narrow its help text.

    Re-exported at ``dotagents.cli.DotAgentsArgs``; an overlay-shipped command
    module (which always runs inside a real ``dotagents`` process) should use
    that import."""

    global_scope: bool = False
    "Use the user scope (~/.agents) instead of the project scope."
    ("--global", "-g")

    agents_dir: "Optional[Path]" = None
    "Store root override (default: ~/.agents for -g, else <project>/.agents)."
    ("--agents-dir",)

    def resolve_scope(self, *, project_root: "str | os.PathLike | None" = None):
        """This command's resolved ``Scope``, from the two fields above."""
        from dotagents._scope import resolve_scope

        return resolve_scope(
            self.global_scope, agents_dir=self.agents_dir, project_root=project_root,
        )


#: The store's own config file, relative to the store. The only file besides
#: `cmds/` that may live in `<store>/dotagents/` (D92/D93), and written only when
#: there is something to record: today the base an `init --from` used.
STORE_CONFIG = "dotagents/config.toml"


def read_store_config(dest: "str | os.PathLike[str]") -> "dict[str, str]":
    """The string keys of `<dest>/dotagents/config.toml` ({} when absent or
    unreadable). A one-level `key = "value"` file: read with tomllib where the
    stdlib has it, else line by line (the only form `write_store_config`
    produces)."""
    import json

    path = Path(dest) / STORE_CONFIG
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError:
        return {}
    try:
        import tomllib  # 3.11+

        data = tomllib.loads(text)
        return {k: v for k, v in data.items() if isinstance(v, str)}
    except ImportError:
        pass
    except ValueError:
        return {}
    out: "dict[str, str]" = {}
    for line in text.splitlines():
        m = re.match(r'^\s*([A-Za-z0-9_-]+)\s*=\s*("(?:[^"\\]|\\.)*")\s*(?:#.*)?$', line)
        if m:
            try:
                out[m.group(1)] = json.loads(m.group(2))
            except ValueError:
                pass
    return out


def write_store_config(dest: "str | os.PathLike[str]", values: "dict[str, str]") -> Path:
    """Write `values` as `<dest>/dotagents/config.toml` (TOML basic strings;
    JSON string escapes are valid TOML ones)."""
    import json

    from dotagents._fs import write_text_lf

    lines = ["# dotagents store config -- written by `dotagents init --from`."]
    lines += ["%s = %s" % (k, json.dumps(v, ensure_ascii=False)) for k, v in sorted(values.items())]
    path = Path(dest) / STORE_CONFIG
    path.parent.mkdir(parents=True, exist_ok=True)
    write_text_lf(path, "\n".join(lines) + "\n")
    return path


def store_base(dest: "str | os.PathLike[str]", logger=None) -> Path:
    """The base overlay this store's block is composed from: the `base` an
    `init --from` recorded in its config, else the bundled one. A recorded base
    that no longer resolves falls back to the bundled one with a warning."""
    recorded = read_store_config(dest).get("base")
    if not recorded:
        return BASE_ROOT
    try:
        return _resolve_from(recorded, BASE_ROOT, logger=logger)
    except SystemExit as exc:
        if logger is not None:
            logger.warning("recorded base %r unusable (%s); using the bundled one", recorded, exc)
        return BASE_ROOT


def _apply_base(
    src: Path, dest: Path, force: bool, dry_run: bool, logger,
    agents: "list[str] | None" = None,
    wire_hooks: bool = False,
    powershell_env_hook: bool = False,
    project: bool = False,
    scope_level: "str | None" = None,
    project_root: "Path | None" = None,
) -> None:
    """Lay down the base: managed-block merge AGENTS.md (rendered for this
    store) and, for Claude, the `@` include in its own config dir. `init`'s
    body. It creates no `dotagents/` dir and no design log: a scope's
    `dotagents/cmds/` exists once its owner adds a command module there.

    With `wire_hooks`, each active agent also gets its hooks merged and the shared
    skills dir linked into its config dir (a no-op for adapters that don't
    implement it). Done here because this is where the active-agent list is
    already resolved."""
    from dotagents import _agents
    from dotagents._overlays import Overlay

    # Compose over the overlays already installed in this store, exactly as
    # `overlays add/remove/sync` do (`recompose_overlay_block`): a re-run of
    # `init` refreshes the block without stripping their rules and routing.
    base_agents = _compose_block(
        base_agents_text(src, dest, project=project),
        Overlay.discover(Path(dest) / "overlays"), logger,
    )

    active_agents = []
    if agents:
        unknown = [name for name in agents if _agents.get_agent(name) is None]
        if unknown:
            raise SystemExit(
                "error: unknown agent(s) %s (known: %s)"
                % (", ".join(unknown), ", ".join(a.name for a in _agents.get_all_agents()))
            )
        active_agents = [_agents.get_agent(name) for name in agents]
    else:
        # Default: all detected + claude
        all_agents = _agents.get_all_agents()
        active_agents = [a for a in all_agents if a.detect_env(os.environ)]
        if not any(a.name == "claude" for a in active_agents):
            active_agents.append(_agents.ClaudeAgent())

    for agent in active_agents:
        if scope_level is not None:
            agent.scope_level = scope_level
            agent.project_root = project_root
        if isinstance(agent, _agents.ClaudeAgent):
            agent.powershell_env_hook = powershell_env_hook

    if wire_hooks:
        # Read every settings file the hooks merge into BEFORE anything is
        # written: a corrupt one (invalid JSON, not UTF-8) raises here, not
        # after AGENTS.md and the includes, which left a half-initialised store.
        # A dry run of the same wiring reads exactly those files and writes none.
        for agent in active_agents:
            agent.wire_hooks(dest, dry_run=True, logger=_QUIET)

    if dry_run:
        logger = _DryRunLog(logger)

    # The store's AGENTS.md is written HERE, once, whatever agents are
    # selected: `context` and `overlays` depend on it. Adapters only add their
    # harness's last mile to it (an include, a pointer).
    from dotagents._merge import merge_block, timestamped_backup_root

    branch = merge_block(
        Path(dest) / "AGENTS.md", base_agents, force=force, dry_run=dry_run,
        backup_root=timestamped_backup_root(Path(dest)) if force else None,
    )
    logger.info("%s: AGENTS.md", branch)

    for agent in active_agents:
        agent.write_base_config(dest, dry_run=dry_run, logger=logger)
        if wire_hooks:
            agent.wire_hooks(dest, dry_run=dry_run, logger=logger)


class _Quiet(object):
    """A logger that drops everything (the settings pre-flight's)."""

    def __getattr__(self, name):
        return lambda *args, **kwargs: None


_QUIET = _Quiet()

#: What a dry run says for each `_merge` result ("created" read as done).
_DRY_RUN_BRANCH = {
    "created": "would create",
    "block-inserted": "would insert the block",
    "block-refreshed": "would refresh the block",
    "removed": "would remove",
    "replaced (--force)": "would replace (--force)",
    "replaced (--force, backed up)": "would replace (--force, with a backup)",
}


class _DryRunLog(object):
    """``logger`` for a dry run: a ``"%s: ..."`` record whose first argument
    is a `_merge` result (the form `init` and every adapter log a write in)
    says what WOULD happen instead of reading as done. Anything else passes
    through unchanged."""

    def __init__(self, logger):
        self._logger = logger

    def __getattr__(self, name):
        return getattr(self._logger, name)

    def info(self, msg, *args, **kwargs):
        if args and isinstance(msg, str) and msg.startswith("%s: ") and args[0] in _DRY_RUN_BRANCH:
            args = (_DRY_RUN_BRANCH[args[0]],) + tuple(args[1:])
        kwargs["stacklevel"] = kwargs.get("stacklevel", 1) + 1  # the caller's line, not this one
        self._logger.info(msg, *args, **kwargs)


#: Where a dotagents checkout keeps its base overlay, relative to the checkout root.
CHECKOUT_BASE = "src/dotagents/_overlay"


def _base_overlay_dir(target: Path, shown: str) -> Path:
    """The base overlay at ``target``: ``target`` itself when it carries a
    block template (``dotagents/templates/AGENTS.md``, or ``AGENTS.md`` at its
    root), or its ``src/dotagents/_overlay`` when ``target`` is a dotagents
    checkout. Anything else is a usage error naming ``shown``."""
    if (target / BASE_AGENTS_TEMPLATE).is_file():
        return target
    checkout = target / CHECKOUT_BASE
    if (checkout / BASE_AGENTS_TEMPLATE).is_file():
        return checkout
    if (target / "AGENTS.md").is_file():
        return target
    raise SystemExit(
        "error: --from %s is not a base overlay: it has no %s (or AGENTS.md at its "
        "root), and it is not a dotagents checkout (%s/%s)"
        % (shown, BASE_AGENTS_TEMPLATE, CHECKOUT_BASE, BASE_AGENTS_TEMPLATE)
    )


def _resolve_from(
    from_arg: "str | None", default: Path, *, cache_root: "Path | None" = None, logger=None
) -> Path:
    """Resolve ``--from`` to a LOCAL base overlay directory.

    ``from_arg`` is a local directory, or an overlay source spec as ``overlays``
    reads one (``_sources``: a ``file:`` / ``http(s):`` / ``sftp:`` / ``s3:`` /
    ``zip:`` URI -- the ``uri`` extra for anything but ``file:`` -- or a git
    repository ``<repo>[@ref][#path]``). A remote is materialized into
    ``cache_root`` (default ``<user store>/.cache/overlays``, the overlay
    cache) and used from there. Either may be the base overlay itself or a
    dotagents checkout (its ``src/dotagents/_overlay``). Every failure is a
    ``SystemExit`` with a one-line message; credentials in a URL never reach it."""
    if from_arg is None:
        return default
    from dotagents import _sources

    candidate = Path(from_arg).expanduser()
    if candidate.exists():
        return _base_overlay_dir(candidate, from_arg)
    try:
        spec = _sources.parse_spec(from_arg)
    except ValueError as exc:
        raise SystemExit("error: --from %r: %s" % (from_arg, exc))
    if spec.kind == "dir":
        raise SystemExit("error: --from path does not exist: %s" % from_arg)
    shown = spec.display()
    if cache_root is None:
        cache_root = resolve_user_store(None) / ".cache" / "overlays"
    try:
        target = _sources.locate(spec, _sources.SourceCache(Path(cache_root), logger))
    except SystemExit as exc:
        message = str(exc.code if exc.code is not None else exc)
        if message.startswith("error: "):
            message = message[len("error: "):]
        raise SystemExit("error: --from %s: %s" % (shown, message)) from None
    if not target.is_dir():
        raise SystemExit("error: --from %s is a file, not a base overlay directory" % shown)
    return _base_overlay_dir(target, shown)


def recorded_from(from_arg: str, logger=None) -> str:
    """How ``--from`` is recorded in the store config: a local path made
    absolute (so it resolves from any cwd), anything else with its credentials
    and query values removed -- the store may be kept in git. A record that
    lost its credentials is fetched without them later, which is said once."""
    from dotagents._sources import redact

    local = Path(from_arg).expanduser()
    if local.exists():
        return str(local.resolve())
    shown = redact(from_arg)
    if shown != from_arg and logger is not None:
        logger.warning(
            "the recorded base %s leaves out the URL's credentials: later `init` and "
            "`overlays` runs fetch it without them (use a credential helper or "
            "netrc, or a local copy)", shown,
        )
    return shown
